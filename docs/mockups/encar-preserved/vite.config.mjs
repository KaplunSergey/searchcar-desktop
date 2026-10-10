import {readFileSync} from 'node:fs';
import {fileURLToPath} from 'node:url';
import ts from 'typescript';
import {defineConfig} from 'vite';
import react from '@vitejs/plugin-react';

const root=fileURLToPath(new URL('.',import.meta.url));
const app=fileURLToPath(new URL('../../../app/',import.meta.url));
// Read the existing components; never edit or mount production Page/AuthRoot.
const functions=['formatDate','formatMoney','formatKnownMoney','isMonthlyOffer','monthlyOfferLabel','monthlyOfferTerm','formatOfferPrice','formatProjectPrice','projectPriceSummary','scanStatusKey','carStatusText','reportProjectNames','Button','Stat','PriceMovement','carCondition','reportPriority','changeTone','changeFieldLabel','changeValue','ReportPrice','ReportList','failureText','FailureList','Projects','Project','Favorites','CarListRow','ScanReportSection','Scans'];
export function componentSource(){
 const raw=readFileSync(app+'page.tsx','utf8'),file=ts.createSourceFile('page.tsx',raw,ts.ScriptTarget.Latest,true,ts.ScriptKind.TSX);
 const selected=file.statements.filter(node=>ts.isTypeAliasDeclaration(node)||
   ts.isFunctionDeclaration(node)&&functions.includes(node.name?.text)||
   ts.isVariableStatement(node)&&node.declarationList.declarations.some(d=>d.name.getText(file)==='REPORT_CHANGE_TYPES'));
 for(const name of functions)if(!selected.some(n=>ts.isFunctionDeclaration(n)&&n.name?.text===name))throw Error('preview_component_missing:'+name);
 const body=selected.map(n=>n.getText(file)).join('\n').replaceAll('window.localStorage','previewStorage').replaceAll('refetchInterval: 5000','refetchInterval: false');
 return `import {useState,useMemo,useRef,useEffect} from 'react';
 import {useQuery,useMutation,useQueryClient} from '@tanstack/react-query';
 import {filterReportItems,reportProjectIds} from '${app}report-filter.js';
 import {formatSeatCount,translateVehicleSpec,translateOptionCode} from '${app}vehicle-dictionary.ts';
 import {previewRequest as request,previewStorage,previewNotice} from '${root}runtime.js';
 const assetUrl=()=>'';const openExternalUrl=async()=>previewNotice();\n${body}\nexport {Projects,Project,Favorites,Scans};`;
}
export default defineConfig({root,plugins:[{
 name:'read-existing-encar-components',enforce:'pre',
 resolveId(id){if(id==='virtual:encar-components')return '\0encar-components.tsx';},
 load(id){if(id==='\0encar-components.tsx')return componentSource();}
},react()],define:{'process.env.NODE_ENV':JSON.stringify('production')},
 build:{outDir:'dist',emptyOutDir:true,minify:false,
 lib:{entry:root+'entry.jsx',formats:['es'],fileName:()=> 'encar-preview.js'}}});
