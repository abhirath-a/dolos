import {readFile,writeFile} from 'node:fs/promises';
const source=new URL('../../dolos/migrations/0002_famous_cases.json',import.meta.url);
const target=new URL('../public/cases.json',import.meta.url);
const dataset=JSON.parse(await readFile(source,'utf8'));
await writeFile(target,JSON.stringify(dataset));
