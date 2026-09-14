import {readFile, writeFile} from 'node:fs/promises';
import assert from 'node:assert/strict';
const here = new URL('./', import.meta.url);
const old = JSON.parse(await readFile(new URL('source-matrix.json', here), 'utf8'));
const duck = JSON.parse(await readFile(new URL('afcd-duck-evidence.json', here), 'utf8'));
const excluded = {N07:'移出：保留干小米',N63:'移出：不增加芝麻',N28:'并入既有豆腐',N29:'并入既有豆腐',N30:'并入既有豆腐',N35:'转私人包装标签录入',N36:'转私人包装标签录入'};
const records = structuredClone(old.records.filter(r => !(r.candidate in excluded)));
const keys = ['kcal','protein_g','carbs_g','fat_g','fiber_g'];
function replaceMext(id, sourceId, item, name, values, raw, note) {
 const r = records.find(r=>r.candidate===id);
 Object.assign(r,{status:'reference_verified',proposedName:r.candidateName,provider:'MEXT',sourceId,
  note,nutrients:Object.fromEntries(keys.map((k,i)=>[k,values[i]])),diagnostics:[],
  source:{name,url:`https://fooddb.mext.go.jp/details/details.pl?ITEM_NO=${item}`,snapshot:'日本食品標準成分表（八訂）増補2023年',
   license:'MEXT food-composition reuse with source attribution',extraction:'Official HTML readback 2026-09-13; numeric transcription; parentheses preserved for estimates',
   fields:keys.map((key,i)=>({key,value:values[i],raw:raw[i],unit:i?'g':'kcal',sourceField:['エネルギー','たんぱく質','炭水化物','脂質','食物繊維総量'][i]}))}});
}
replaceMext('N03','01048','1_01048_7','Common wheat / yellow alkaline noodles, boiled',
 [133,4.9,29.2,0.6,2.8],['133','4.9','29.2','0.6','(2.8)'],
 '熟小麦面条采用煮熟碱水小麦面作为同类代表，不含汤、油、浇头；不是全国平均，不与干面或乌冬任意求平均。纤维2.8为源表估计值。');
replaceMext('N31','04052','4_04052_7','Soybeans / soy milk, regular',
 [43,3.6,2.3,2.8,0.9],['43','3.6','2.3','2.8','0.9'],
 '基础豆乳同类参考，不使用加糖加油调制豆乳；浓度存在差异，100g不是100mL。食品定义：https://fooddb.mext.go.jp/details/foodInfo.pl?ITEM_NO=4_04052_7&VIEW_POPUP=1');
const bun = records.find(r=>r.candidate==='N05');
assert.equal(bun.sourceId,'R2400201');
Object.assign(bun,{status:'reference_verified',proposedName:bun.candidateName,note:'按用户决定采用白馒头同类参考；不要求品牌配方，不宣称全国均值或无糖。'});
const d = records.find(r=>r.candidate==='N15');
Object.assign(d,{status:'reference_verified_license_conditions',provider:'FSANZ AFCD Release 3',sourceId:'F009805',proposedName:'鸭胸肉（去皮去可见脂肪，生）',
 nutrients:{kcal:383/4.184,protein_g:21.2,carbs_g:0,fat_g:0.6,fiber_g:0},diagnostics:[],
 note:'2019年澳大利亚5州8份样品混合分析，去皮、去筋、去可见脂肪生胸肉。碳水为可利用碳水口径、纤维为0；源表的零包括估算零，不伪称全部实测。能量383kJ/4.184换算，保留未舍入结果。',
 source:{name:'Duck, breast, lean flesh, raw',url:'https://www.foodstandards.gov.au/science-data/food-nutrient-databases/afcd/data-files',
  detailUrl:'https://www.foodstandards.gov.au/science-data/food-nutrient-databases/afcd/search/food/F009805',evidence:duck,
  license:'AFCD specific Data User Licence Agreement (based on CC BY-SA 3.0 AU); do not substitute generic website CC BY 4.0',
  licenseUrl:'https://www.foodstandards.gov.au/science-data/monitoringnutrients/afcd/datauserlicenceagreement',
  releaseGate:'Before distribution, attach AFCD attribution, licence link, required limitation/Australian-data notices and change notice; review data-layer licence compatibility. This audit does not authorise runtime import.'}});
for(const r of records) {
 assert(!['hold','definition_review'].includes(r.status),r.candidate);
 assert(keys.slice(0,4).every(k=>Number.isFinite(r.nutrients[k])&&r.nutrients[k]>=0));
 const n=r.nutrients, e=n.protein_g*4+n.carbs_g*4+n.fat_g*9;
 r.energy449={calculated:e,difference:e-n.kcal,relativeDifference:n.kcal?(e/n.kcal-1)*100:null};
}
assert.equal(records.length,59); assert.equal(new Set(records.map(r=>r.candidate)).size,59);
assert.deepEqual(bun.nutrients,{kcal:248,protein_g:8.1,carbs_g:51.3,fat_g:1.2,fiber_g:1.1});
const data={version:3,reviewed:'2026-09-13',scope:'reference audit only, no runtime changes',counts:{newPublic:59,existingUnchanged:21,targetPublic:80,excluded:7},excluded,records};
await writeFile(new URL('source-matrix-v3.json',here),JSON.stringify(data,null,2)+'\n');
const rows=records.map(r=>`| ${r.candidate} | ${r.proposedName} | ${r.provider} ${r.sourceId} | ${r.nutrients.kcal.toFixed(2).replace(/\.00$/,'')} | ${r.nutrients.protein_g} | ${r.nutrients.carbs_g} | ${r.nutrients.fat_g} |`);
await writeFile(new URL('source-matrix-v3.md',here),`# 食品来源核验 v3\n\n59项新增参考来源已匹配，21项既有定义不变。不是生产导入完成。N15的数据许可发布要求见 remaining-sources.md。下表为每100g可食部分，热量kcal、其余g；仅展示舍入，JSON保留原值及证据。\n\n| 编号 | 食品 | 来源 | 热量 | 蛋白质 | 碳水 | 脂肪 |\n|---|---|---|---:|---:|---:|---:|\n${rows.join('\n')}\n`);
console.log('PASS: 59 references, 7 exclusions, original baseline preserved; AFCD release licence gate recorded.');
