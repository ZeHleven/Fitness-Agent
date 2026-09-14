import {readFile, writeFile} from 'node:fs/promises';
import {createHash} from 'node:crypto';
import assert from 'node:assert/strict';
import {selections, mext} from './selections.mjs';

const here = new URL('./', import.meta.url);
const sources = new URL('../../../food-library-planning-20260913/sources/', here);
const readJSON = async (url) => JSON.parse(await readFile(url, 'utf8'));
const tfdaRaw = await readJSON(new URL('tfda-raw.json', sources));
const usdaRaw = (await readJSON(new URL('usda-raw.json', sources))).SRLegacyFoods;
const manifest = await readJSON(new URL('download-manifest.json', sources));
for (const archive of manifest.sources) {
 const bytes = await readFile(new URL(archive.file, sources));
 assert.equal(createHash('sha256').update(bytes).digest('hex'), archive.sha256);
}
const candidates = [...(await readFile(new URL('../food-library-candidates.md', here),'utf8')).matchAll(/^\| (N\d+) \| ([^|]+) \|/gm)];
assert.equal(candidates.length,66);
const names = new Map(candidates.map(x=>[x[1],x[2].trim()]));
assert.equal(selections.length,66); assert.equal(new Set(selections.map(x=>x[0])).size,66);
const keys = ['kcal','protein_g','carbs_g','fat_g','fiber_g'];
const tfdaKeys = ['熱量','粗蛋白','總碳水化合物','粗脂肪','膳食纖維'];
const usdaKeys = [1008,1003,1005,1004,1079];
const labels = {matched:'来源匹配',definition_review:'需明确规格',hold:'暂不放行'};
const records = [];
let sourceChecks = 0;
function finiteValue(raw, unit, expected) {
 assert.equal(unit,expected);
 if (raw === null || raw === undefined || (typeof raw === 'string' && !raw.trim())) return null;
 const value = Number(raw);
 assert(Number.isFinite(value) && value >= 0, `Invalid nutrient ${raw}`);
 return value;
}
// Guard the actual parser, including zeros versus missing values.
assert.equal(finiteValue('', 'g','g'),null);
assert.equal(finiteValue('  ', 'g','g'),null);
assert.equal(finiteValue(null, 'g','g'),null);
assert.equal(finiteValue('0', 'g','g'),0);
assert.throws(()=>finiteValue('NaN','g','g'));
assert.throws(()=>finiteValue('Infinity','g','g'));
assert.throws(()=>finiteValue('-1','g','g'));
assert.throws(()=>finiteValue('1','mg','g'));
for (const [candidate,provider,id,status,proposedName,note] of selections) {
 assert(names.has(candidate)); assert(status in labels);
 const r = {candidate,candidateName:names.get(candidate),status,proposedName,note,provider,sourceId:id,reviewed:'2026-09-13',basis:'100 g edible portion',nutrients:null,source:null,diagnostics:[]};
 if (provider === 'TFDA') {
  const rows = tfdaRaw.filter(x=>x['整合編號']===id);
  assert(rows.length, `Missing TFDA ${id}`);
  const sample = rows[0];
  assert(rows.every(x=>x['樣品名稱']===sample['樣品名稱'] && x['內容物描述']===sample['內容物描述']));
  const fields = tfdaKeys.map((field,i)=>{
   const hits=rows.filter(x=>x['分析項']===field); assert.equal(hits.length,1,`${id}/${field} duplicate or missing`);
   const x=hits[0]; const value=finiteValue(x['每100克含量'],x['含量單位'],i===0?'kcal':'g'); sourceChecks++;
   return {key:keys[i],sourceField:field,value,raw:x['每100克含量'],unit:x['含量單位'],samples:x['樣本數'],standardDeviation:x['標準差']};
  });
  const corrected=rows.find(x=>x['分析項']==='修正熱量');
  r.nutrients=Object.fromEntries(fields.map(x=>[x.key,x.value]));
  r.source={name:sample['樣品名稱'],english:sample['樣品英文名稱'],description:sample['內容物描述'],discardPercentRaw:sample['廢棄率'],fields,correctedEnergyRaw:corrected?.['每100克含量']??null,url:'https://data.gov.tw/dataset/8543',archive:'tfda-foods.json',snapshot:'2026-09-13',license:'Government Data Open License v1'};
 } else if (provider === 'USDA') {
  const hits=usdaRaw.filter(x=>x.fdcId===id);assert.equal(hits.length,1,`Missing/duplicate USDA ${id}`);
  const sample=hits[0];
  const fields=usdaKeys.map((field,i)=>{
   const hits=sample.foodNutrients.filter(x=>x.nutrient.id===field);assert(hits.length<=1);assert(hits.length||i===4,`Missing mandatory ${id}/${field}`);
   const x=hits[0]; sourceChecks++;
   return {key:keys[i],sourceField:field,value:x?finiteValue(x.amount,x.nutrient.unitName,i===0?'kcal':'g'):null,raw:x?.amount??null,unit:x?.nutrient.unitName??'g',derivation:x?.foodNutrientDerivation??null};
  });
  r.nutrients=Object.fromEntries(fields.map(x=>[x.key,x.value]));
  r.source={name:sample.description,fields,ndbNumber:sample.ndbNumber,publicationDate:sample.publicationDate,url:`https://fdc.nal.usda.gov/food-details/${id}/nutrients`,archive:'usda-sr-legacy-2018.zip',snapshot:'SR Legacy 2018 final; FDC record publication dates retained separately',license:'CC0'};
 } else if (provider === 'MEXT') {
  const sample=mext[id];assert(sample);assert.equal(sample.values.length,5);
  const fields=sample.values.map((value,i)=>({key:keys[i],value:finiteValue(value,i===0?'kcal':'g',i===0?'kcal':'g'),raw:sample.raw[i],unit:i===0?'kcal':'g',sourceField:['エネルギー','たんぱく質','炭水化物','脂質','食物繊維総量'][i]}));
  sourceChecks+=5;r.nutrients=Object.fromEntries(fields.map(x=>[x.key,x.value]));
  r.source={name:sample.name,english:sample.english,fields,url:`https://fooddb.mext.go.jp/details/details.pl?ITEM_NO=${sample.item}`,snapshot:'日本食品標準成分表（八訂）増補2023年',license:'MEXT food-composition reuse with source attribution',extraction:'manual transcription of official HTML, independently readback; no local HTML archive (TLS download failed)'};
 } else assert.equal(status,'hold');
 if(r.nutrients){
  const n=r.nutrients;
  for(const k of keys.slice(0,4))assert(n[k]!==null,`${candidate} missing ${k}`);
  for(const k of keys.slice(1))assert(n[k]===null || n[k]<=100,`${candidate} impossible grams`);
  assert(n.kcal<=1000);
  if(n.fiber_g===null) r.diagnostics.push('纤维缺失；必须保留null，不补零');
  if(n.fiber_g!==null && n.fiber_g>n.carbs_g+0.1) {
   r.diagnostics.push('纤维大于总碳水，需来源口径复核'); assert.equal(status,'hold');
  }
  const macroEnergy=n.protein_g*4+n.carbs_g*4+n.fat_g*9;
  r.energy449={calculated:Math.round(macroEnergy*100)/100,difference:Math.round((macroEnergy-n.kcal)*100)/100,relativeDifference:n.kcal?Math.round((macroEnergy/n.kcal-1)*10000)/100:null};
  if(n.kcal>0 && Math.abs(macroEnergy/n.kcal-1)>0.2)r.diagnostics.push('4/4/9差异超过20%：只提示核对，不改写源热量');
 }
 records.push(r);
}
const counts=Object.fromEntries(Object.keys(labels).map(k=>[k,records.filter(x=>x.status===k).length]));
const payload={scope:'source audit only; no runtime import authorization',version:1,reviewed:'2026-09-13',counts,sourceChecks,archiveManifest:manifest.sources,records};
await writeFile(new URL('source-matrix.json',here),JSON.stringify(payload,null,2)+'\n');
let md='# 新增食品来源与生熟状态核验表\n\n2026-09-13。66 项全部有核验结论，但不等于全部放行。此表仅用于审查；未导入数据库、未修改正式食品或历史餐次。\n\n';
md+=`结论：来源匹配 ${counts.matched} 项；需明确规格 ${counts.definition_review} 项；暂不放行 ${counts.hold} 项。\n\n`;
md+='“来源匹配”仅表示在下列明确规格下具有完整同源热量、蛋白质、碳水和脂肪数据，不表示所有品种相同，也不代表均为直接实测。拟用名称是审核建议，尚未修改已通过的预览。\n\n';
md+='所有数值按 **100g 可食部**；热量为 kcal，其余为 g。表中营养值顺序：热量／蛋白质／碳水／脂肪／纤维。“—”为来源缺项。需明确规格和暂不放行项中的数值只供比较，不可按原候选名称入库。\n\n';
md+='TFDA 本轮统一审查其“熱量”字段，另将“修正熱量”保存在 JSON 中，不择低取值，不自行重新计算。USDA 保留 nutrient 1008；MEXT 保留一般成分的能量、蛋白质、总碳水、脂质，不混入氨基酸蛋白或可利用碳水。\n\n';
md+='| 编号 | 原候选 → 拟用精确名称 | 结论 | 来源及编号 | 每100g：kcal/P/C/F/纤维 | 生熟、可食部及采用条件 |\n|---|---|---|---|---|---|\n';
for(const r of records){const source=r.source?`[${r.provider} ${r.sourceId}](${r.source.url})`:'见阻断项说明';const nums=r.nutrients?keys.map(k=>r.nutrients[k]??'—').join(' / '):'—';md+=`| ${r.candidate} | ${r.candidateName} → ${r.proposedName} | ${labels[r.status]} | ${source} | ${nums} | ${r.note} |\n`;}
md+='\n## 核验与原始证据\n\n';
md+='- JSON 保留每个选中样品的原名、原字段、原值、缺项、来源代码、可食部描述、版本或快照日期及来源链接；未知来源方法不被标为“实测”。\n';
md+=`- 两个官方 ZIP 的 SHA256 已重新校验；${sourceChecks} 个营养字段完成逐项提取／存在性／单位／有限非负数检查。TFDA 直接从原始行核对，遇重复字段拒绝生成；不依赖可能覆盖重复值的旧索引。\n`;
md+='- 生／熟四项土豆与紫薯各有独立来源记录；不会按能量比、废弃率或统一失水率互相换算。\n';
md+='- MEXT 五个条目为官方 HTML 手工转录并读回核验。下载 HTML 因本地 TLS 失败，未声称保留其本地网页哈希。USDA/TFDA 为离线原始数据提取。\n';
md+='- JSON 保留4/4/9比较作为数据诊断，不当作更正依据；保留来源修约和理论零值标记。\n';
md+='\n## 当前数据诊断\n\n';
for(const r of records.filter(r=>r.diagnostics.length))md+=`- ${r.candidate} ${r.proposedName}：${r.diagnostics.join('；')}。\n`;
md+='\n完整许可、未通过条目的补救路径及实施边界见 [核验说明](./README.md)。\n';
await writeFile(new URL('source-matrix.md',here),md);
console.log(JSON.stringify({counts,sourceChecks,records:records.length,diagnostics:records.filter(r=>r.diagnostics.length).map(r=>({id:r.candidate,warnings:r.diagnostics})),mandatoryNutrientsComplete:records.filter(r=>r.nutrients).length},null,2));
