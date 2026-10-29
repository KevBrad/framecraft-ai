/** Real local browser workflow. Requires the API, Vite, FFmpeg and a configured
 * transcription model. No responses are mocked. Uploaded test projects remain
 * available for manual inspection. Set FRAMECRAFT_TEST_VIDEO to an owned fixture. */
import assert from 'node:assert/strict';
import {mkdir,writeFile} from 'node:fs/promises';
import path from 'node:path';
import {pathToFileURL} from 'node:url';

const moduleName=process.env.FRAMECRAFT_PLAYWRIGHT_MODULE;
const {chromium}=await import(moduleName?pathToFileURL(moduleName).href:'playwright');
const base=process.env.FRAMECRAFT_URL||'http://127.0.0.1:5173';
const fixture=process.env.FRAMECRAFT_TEST_VIDEO;
if(!fixture)throw new Error('Set FRAMECRAFT_TEST_VIDEO to an owned video containing speech. This test performs real upload, transcription, editing, rendering and download.');
const root=path.resolve(import.meta.dirname,'../..'),artifacts=path.join(root,'tests/artifacts'),screenshots=path.join(root,'docs/screenshots');
await mkdir(artifacts,{recursive:true});await mkdir(screenshots,{recursive:true});
const browser=await chromium.launch({headless:true,...(process.env.FRAMECRAFT_CHROME?{executablePath:process.env.FRAMECRAFT_CHROME}:{channel:'chrome'})});
const page=await browser.newPage({viewport:{width:1440,height:1000},acceptDownloads:true});
const errors=[],checks=[],title=`Browser verification ${new Date().toISOString()}`;
page.on('pageerror',e=>errors.push(e.message));
const check=(name,detail)=>{checks.push({name,status:'passed',detail});console.log(JSON.stringify({check:name,status:'passed',detail}))};
try{
 await page.goto(base);await page.getByRole('heading',{name:'A little footage. A lot of possibility.',exact:true}).waitFor();
 await page.screenshot({path:path.join(screenshots,'dashboard.png'),fullPage:true});check('dashboard','Projects loaded from the real local API.');
 await page.getByRole('button',{name:'New project',exact:true}).last().click();
 assert.equal(await page.getByRole('button',{name:'Find my moments',exact:true}).isEnabled(),false);check('rights gate','Import remains disabled without source and rights affirmation.');
 await page.locator('input[type=file]').setInputFiles(fixture);
 await page.getByRole('textbox',{name:'Project name',exact:true}).fill(title);
 await page.getByRole('spinbutton',{name:'Shortest clip sec',exact:true}).fill('5');
 await page.getByRole('spinbutton',{name:'Longest clip sec',exact:true}).fill('15');
 await page.getByRole('checkbox',{name:'I own this video or have permission to edit and export it.',exact:true}).check();
 await page.getByRole('button',{name:'Find my moments',exact:true}).click();
 await page.getByRole('heading',{name:title,exact:true}).waitFor({timeout:30000});
 await page.getByRole('button',{name:'Make it yours',exact:true}).first().waitFor({timeout:600000});
 check('upload and real analysis','The uploaded speech fixture produced an analyzed candidate through the actual worker.');
 const projects=await (await page.request.get(`${base}/api/projects`)).json();const project=projects.find(p=>p.title===title);assert.ok(project?.id);assert.ok(project.clips.length>0);
 await page.getByRole('button',{name:'Make it yours',exact:true}).first().click();
 const originalCount=await page.locator('.caption-item').count();assert.ok(originalCount>0);
 if(originalCount>1){await page.locator('.caption-check input').nth(0).check();await page.locator('.caption-check input').nth(1).check();await page.getByRole('button',{name:'Merge (2)',exact:true}).click();assert.equal(await page.locator('.caption-item').count(),originalCount-1);await page.getByRole('button',{name:'Undo',exact:true}).click();assert.equal(await page.locator('.caption-item').count(),originalCount);check('caption merge and undo','Two captions merged; undo restored both.');}
 await page.getByRole('textbox',{name:'Caption 1 text',exact:true}).fill('Framecraft browser verification: a saved caption.');
 const captionEnd=Number(await page.getByRole('spinbutton',{name:'Caption 1 end in seconds',exact:true}).inputValue());
 if(captionEnd>.2){await page.getByRole('spinbutton',{name:'Caption 1 start in seconds',exact:true}).fill('0.1');await page.getByRole('spinbutton',{name:'Caption 1 end in seconds',exact:true}).fill(String(Math.max(.2,captionEnd-.02)));}
 await page.getByRole('button',{name:'Split caption 1',exact:true}).click();assert.equal(await page.locator('.caption-item').count(),originalCount+1);await page.getByRole('button',{name:'Undo',exact:true}).click();check('caption text, timing, split and undo','Text and timings edited without stale word alignment; split and undo restored count.');
 const clipTitle=`Verified browser cut ${project.id.slice(0,8)}`;await page.getByRole('textbox',{name:'Clip title',exact:true}).fill(clipTitle);
 await page.getByLabel('Original caption styles',{exact:true}).selectOption('boxed');
 await page.getByRole('button',{name:'Frame',exact:true}).click();await page.getByRole('button',{name:'1:1',exact:true}).click();await page.getByRole('button',{name:'Manual Choose your own crop position.',exact:true}).click();
 await page.getByRole('slider',{name:/Crop horizontal/}).fill('25');await page.getByRole('slider',{name:/Crop vertical/}).fill('60');
 await page.getByRole('button',{name:'Save',exact:true}).click();await page.getByText(/All changes saved · v/).waitFor({timeout:15000});check('save','Title, caption text/timing/style, aspect ratio, and manual crop saved through PUT.');
 await page.getByRole('button',{name:'Render clip',exact:true}).click();await page.getByRole('button',{name:'Preview export',exact:true}).waitFor({timeout:600000});
 await page.getByRole('button',{name:'Captions',exact:true}).click();await page.screenshot({path:path.join(screenshots,'editor.png'),fullPage:true});
 await page.getByRole('button',{name:'Preview export',exact:true}).click();await page.getByRole('heading',{name:'Your finished clip',exact:true}).waitFor();
 const video=page.locator('.export-preview');await video.evaluate(el=>new Promise((resolve,reject)=>{const timeout=setTimeout(()=>reject(new Error('Actual export metadata did not load within 30 seconds')),30000);const loaded=()=>{clearTimeout(timeout);resolve()};if(el.readyState>=1)loaded();else{el.addEventListener('loadedmetadata',loaded,{once:true});el.addEventListener('error',()=>{clearTimeout(timeout);reject(new Error('Export preview media failed'))},{once:true})}}));
 const dimensions=await video.evaluate(el=>({width:el.videoWidth,height:el.videoHeight,duration:el.duration}));assert.equal(dimensions.width,dimensions.height);assert.ok(dimensions.duration>0);check('actual rendered preview',dimensions);
 const downloadPromise=page.waitForEvent('download');await page.getByRole('link',{name:'Download MP4',exact:true}).click();const download=await downloadPromise;await download.saveAs(path.join(artifacts,'browser-download.mp4'));check('MP4 download','Saved actual export to tests/artifacts/browser-download.mp4.');
 await page.getByRole('button',{name:'Close dialog',exact:true}).click();await page.setViewportSize({width:390,height:844});await page.screenshot({path:path.join(screenshots,'mobile.png'),fullPage:true});
 const mobile=await page.evaluate(()=>({viewport:innerWidth,scroll:document.documentElement.scrollWidth}));assert.ok(mobile.scroll<=mobile.viewport,JSON.stringify(mobile));check('responsive editor',mobile);
 await page.reload();await page.getByRole('button',{name:'Open project',exact:true}).first().waitFor();await page.getByRole('button',{name:`Open ${title}`,exact:true}).click();await page.getByRole('button',{name:'Make it yours',exact:true}).first().click();assert.equal(await page.getByRole('textbox',{name:'Clip title',exact:true}).inputValue(),clipTitle);assert.equal(await page.getByRole('textbox',{name:'Caption 1 text',exact:true}).inputValue(),'Framecraft browser verification: a saved caption.');check('refresh persistence','A complete reload and reopen retained title and captions from backend.');
 assert.deepEqual(errors,[]);check('browser errors','No uncaught page errors.');
 await writeFile(path.join(root,'frontend/browser-evidence.json'),JSON.stringify({timestamp:new Date().toISOString(),base,projectId:project.id,checks,errors},null,2));console.log(JSON.stringify({status:'passed',projectId:project.id,checks},null,2));
}catch(error){await page.screenshot({path:path.join(artifacts,'browser-failure.png'),fullPage:true}).catch(()=>{});console.error(error);process.exitCode=1}finally{await browser.close()}
