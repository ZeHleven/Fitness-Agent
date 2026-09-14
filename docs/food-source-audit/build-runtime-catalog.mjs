// Reproducible transform of reviewed facts, never generated nutritional guesses.
import {readFile,writeFile} from 'node:fs/promises';import assert from 'node:assert/strict';
const here=new URL('./',import.meta.url),matrix=JSON.parse(await readFile(new URL('source-matrix-v3.json',here),'utf8'));
const catalog=JSON.parse(await readFile(new URL('browse-mapping.json',here),'utf8'));
const semantic={'主食薯类':'碳水','肉蛋类':'蛋白质','鱼虾水产':'蛋白质','豆类豆制品':'蛋白质','奶类':'乳制品','蔬菜菌菇':'蔬菜','水果':'水果','坚果油脂':'坚果'};
const entries=catalog.filter(c=>c.id.startsWith('old-')).map(c=>({key:c.id,new:false,name:c.name,browse_category:c.category,browse_aliases:c.aliases}));
const limitations='There are limitations associated with food composition databases. Food composition data used in the database or databases may represent an average of the nutrient content of a particular sample of foods and ingredients, determined at a particular time. The nutrient composition of foods and ingredients can vary substantially between batches and brands because of a number of factors, including changes in season, processing practices and ingredient source, and methods of calculation.';
for(const r of matrix.records){const c=catalog.find(c=>c.id===r.candidate);assert(c);
 const n=r.nutrients,info={provider:r.provider,source_id:String(r.sourceId),url:r.source.url,reference_name:r.source.name,
  basis:'每100g可食部分',note:r.note,license:r.source.license,version:r.source.snapshot||'AFCD Release 3, December 2025'};
 if(r.candidate==='N15')Object.assign(info,{attribution:'© Food Standards Australia New Zealand — Australian Food Composition Database, Release 3. This adapted data entry is distributed under the AFCD Data User Licence Agreement.',license_url:r.source.licenseUrl,
  limitations,regional_notice:'Based on Australian data; Australian data may not be appropriate for use in other countries.',
  changes:'Selected fields; food name and notes translated to Chinese; energy converted from 383 kJ using 4.184 kJ/kcal. No endorsement by FSANZ.'});
 entries.push({key:r.candidate,new:true,fields:{name_zh:r.proposedName,name_en:null,category:r.candidate==='N64'?'油脂':semantic[c.category],
  browse_category:c.category,browse_aliases:[...new Set([...c.aliases,c.name].filter(x=>x!==r.proposedName))],
  calories_per_100g:n.kcal,protein_g:n.protein_g,carbs_g:n.carbs_g,fat_g:n.fat_g,fiber_g:n.fiber_g,
  common_portion_g:100,diet_tags:[],is_common_in_china:true,is_active:true,
  source_name:r.provider,source_reference:r.source.url,source_info:info}});
}
assert.equal(entries.length,80);assert.equal(entries.filter(e=>e.new).length,59);
await writeFile(new URL('../../backend/app/data/food_catalog_v1.json',here),JSON.stringify(entries,null,2)+'\n');
console.log('Frozen runtime catalogue: 59 new, 21 legacy metadata only.');
