/** Real browser checks. No API or media responses are mocked.
 * Set FRAMECRAFT_URL to an isolated loopback workspace. For editing/export,
 * supply FRAMECRAFT_TEST_PROJECT_ID and FRAMECRAFT_TEST_DISPOSABLE=1.
 */
import assert from 'node:assert/strict';
import {mkdir,writeFile,stat} from 'node:fs/promises';
import path from 'node:path';
import {pathToFileURL} from 'node:url';
const {chromium}=await import(process.env.FRAMECRAFT_PLAYWRIGHT_MODULE?pathToFileURL(process.env.FRAMECRAFT_PLAYWRIGHT_MODULE).href:'playwright');
const base=process.env.FRAMECRAFT_URL||'http://127.0.0.1:8005';
assert.ok(['localhost','127.0.0.1','[::1]'].includes(new URL(base).hostname),'Use an isolated loopback test server.');
const id=process.env.FRAMECRAFT_TEST_PROJECT_ID;
if(id)assert.equal(process.env.FRAMECRAFT_TEST_DISPOSABLE,'1','Confirm that this is a disposable test project.');
const artifacts=process.env.FRAMECRAFT_ARTIFACTS||path.resolve(import.meta.dirname,'../../tests/artifacts/v4-browser');
await mkdir(artifacts,{recursive:true});
const browser=await chromium.launch({headless:true,...(process.env.FRAMECRAFT_CHROME?{executablePath:process.env.FRAMECRAFT_CHROME}:{channel:'chrome'})});
const page=await browser.newPage({viewport:{width:1440,height:1050},acceptDownloads:true});
const errors=[],checks=[];
page.on('pageerror',error=>errors.push(error.message));
const check=(name,detail)=>checks.push({name,status:'passed',detail});
async function project(){const response=await page.request.get(`${base}/api/projects/${encodeURIComponent(id)}`);assert.ok(response.ok());return response.json();}
async function widthCheck(){const size=await page.evaluate(()=>({width:innerWidth,scroll:document.documentElement.scrollWidth}));assert.ok(size.scroll<=size.width,JSON.stringify(size));return size;}
try{
 await page.goto(base);
 await page.getByRole('button',{name:'New project',exact:true}).last().click();
 assert.equal(await page.getByLabel('Analysis approach',{exact:true}).inputValue(),'3');
 assert.equal(await page.getByLabel('Content type',{exact:true}).count(),0);
 await page.getByLabel('Analysis approach',{exact:true}).selectOption('4');
 assert.equal(await page.getByLabel('Content type',{exact:true}).inputValue(),'auto');
 for(const genre of ['general','podcast','gaming','cinematic','education','sports','comedy','visual','auto']){
  await page.getByLabel('Content type',{exact:true}).selectOption(genre);
  assert.equal(await page.getByLabel('Content type',{exact:true}).inputValue(),genre);
 }
 assert.ok(await page.getByText('Includes moments without dialogue.',{exact:true}).isVisible());
 await page.getByLabel('Analysis approach',{exact:true}).selectOption('3');
 assert.equal(await page.getByLabel('Content type',{exact:true}).count(),0);
 await page.getByLabel('Analysis approach',{exact:true}).selectOption('4');
 await page.getByLabel('Content type',{exact:true}).selectOption('gaming');
 check('opt-in genre controls','Default speech engine, all supported overrides, switching back preserves the established workflow.');
 await page.getByLabel('Analysis approach',{exact:true}).scrollIntoViewIfNeeded();
 await page.screenshot({path:path.join(artifacts,'v4-controls-desktop.png'),fullPage:true});
 await page.setViewportSize({width:390,height:844});
 await page.getByLabel('Analysis approach',{exact:true}).scrollIntoViewIfNeeded();
 check('narrow controls',await widthCheck());
 await page.screenshot({path:path.join(artifacts,'v4-controls-mobile.png'),fullPage:true});
 await page.getByRole('button',{name:'Close dialog',exact:true}).click();
 await page.setViewportSize({width:1440,height:1050});
 if(id){
  const before=await project();
  assert.equal(before.settings.analysis_version,4,'Require an actually processed v4 test project.');
  const active=before.clips.filter(clip=>clip.analysis_active!==false).sort((a,b)=>(b.editorial?.score??b.score/10)-(a.editorial?.score??a.score/10));
  assert.ok(active.length&&active[0].intelligence,'Require real measured candidate evidence.');
  const first=active[0];
  await page.getByRole('button',{name:`Open ${before.title}`,exact:true}).click();
  await page.getByText('Why this moment?',{exact:true}).first().click();
  const evidence=page.getByRole('region',{name:'Source evidence'}).first();
  assert.ok(await evidence.isVisible());
  assert.ok((await evidence.innerText()).includes('Observed in the source'));
  assert.ok((await evidence.innerText()).includes('Experimental'));
  if(first.intelligence.basis==='visual')assert.ok((await evidence.innerText()).includes('does not depend on spoken dialogue'));
  await page.screenshot({path:path.join(artifacts,'v4-evidence-desktop.png'),fullPage:true});
  await page.setViewportSize({width:390,height:844});
  check('narrow source evidence',await widthCheck());
  await evidence.scrollIntoViewIfNeeded();
  await page.screenshot({path:path.join(artifacts,'v4-evidence-mobile.png'),fullPage:true});
  await page.setViewportSize({width:1440,height:1050});
  check('real candidate evidence',{basis:first.intelligence.basis,genre:first.intelligence.genre,signals:first.intelligence.observed_signals.length});
  const jobsBefore=before.jobs.filter(job=>job.kind==='analysis').length;
  await page.getByRole('button',{name:'Analyze again',exact:true}).click();
  assert.equal(await page.getByLabel('Analysis approach',{exact:true}).inputValue(),'4');
  assert.equal(await page.getByLabel('Content type',{exact:true}).inputValue(),before.settings.content_genre);
  await page.getByLabel('Content type',{exact:true}).selectOption('comedy');
  await page.getByRole('button',{name:'Keep current settings',exact:true}).click();
  assert.equal((await project()).jobs.filter(job=>job.kind==='analysis').length,jobsBefore);
  await page.getByRole('button',{name:'Analyze again',exact:true}).click();
  assert.equal(await page.getByLabel('Content type',{exact:true}).inputValue(),before.settings.content_genre);
  await page.getByRole('button',{name:'Keep current settings',exact:true}).click();
  check('safe reanalysis controls','Persisted genre shown; cancelling restores the saved choice and creates no processing job.');
  await page.getByRole('button',{name:'Make it yours',exact:true}).first().click();
  const title=`${first.edit.title.slice(0,165)} · browser verified`;
  await page.getByLabel('Clip title',{exact:true}).fill(title);
  await page.getByRole('button',{name:'Frame',exact:true}).click();
  await page.getByRole('button',{name:'Fit original Keep the full video with letterboxing.',exact:true}).click();
  await page.getByRole('button',{name:'9:16',exact:true}).click();
  await page.getByLabel('Export quality',{exact:true}).selectOption('1080p');
  await page.getByRole('button',{name:'Save',exact:true}).click();
  await page.getByText(/All changes saved · v/).waitFor();
  const saved=(await project()).clips.find(clip=>clip.id===first.id);
  assert.equal(saved.edit.title,title);assert.equal(saved.edit.layout,'fit');assert.equal(saved.edit.output_resolution,'1080p');
  assert.deepEqual(saved.intelligence,first.intelligence,'Editing should preserve analysis evidence.');
  check('saved visual edit',{version:saved.version,layout:saved.edit.layout,resolution:saved.edit.output_resolution});
  await page.getByRole('button',{name:'Render clip',exact:true}).click();
  let output;
  for(let tries=0;tries<900;tries++){
   const current=await project();
   output=current.exports.find(item=>item.clip_id===first.id&&item.version===saved.version);
   if(output)break;
   const failed=current.jobs.find(job=>job.clip_id===first.id&&job.kind==='render'&&job.edit_version===saved.version&&job.status==='failed');
   assert.ok(!failed,failed?.error);
   await new Promise(resolve=>setTimeout(resolve,400));
  }
  assert.ok(output,'The current saved edit must produce a genuine export.');
  await page.locator(`a[href="${output.download_url}"]`).first().waitFor({timeout:15000});
  await page.getByRole('button',{name:'Preview export',exact:true}).waitFor({timeout:15000});
  await page.getByRole('button',{name:'Preview export',exact:true}).click();
  const video=page.locator('.export-preview');
  await video.evaluate(el=>new Promise((resolve,reject)=>{if(el.readyState>=1)resolve();else{el.addEventListener('loadedmetadata',resolve,{once:true});el.addEventListener('error',()=>reject(new Error('Export failed to load')),{once:true});}}));
  const media=await video.evaluate(el=>({width:el.videoWidth,height:el.videoHeight,duration:el.duration}));
  assert.equal(media.width,1080);assert.equal(media.height,1920);assert.ok(media.duration>0);
  const pending=page.waitForEvent('download');
  await page.getByRole('link',{name:'Download MP4',exact:true}).click();
  const download=path.join(artifacts,'framecraft-v4-browser.mp4');await(await pending).saveAs(download);
  assert.ok((await stat(download)).size>1000);
  check('real saved-edit render and download',media);
  await page.getByRole('button',{name:'Close dialog',exact:true}).click();
  await page.reload();
  await page.getByRole('button',{name:`Open ${before.title}`,exact:true}).click();
  await page.getByRole('button',{name:'Make it yours',exact:true}).first().click();
  assert.equal(await page.getByLabel('Clip title',{exact:true}).inputValue(),title);
  check('reopen persistence','Saved title and version remain accessible after reload.');
 }
 assert.deepEqual(errors,[]);
 check('browser errors',errors);
 const result={status:'passed',scope:id?'real-v4-edit-export':'controls-only',projectId:id??null,checks,errors};
 await writeFile(path.join(artifacts,'results.json'),JSON.stringify(result,null,2));
 console.log(JSON.stringify(result,null,2));
}catch(error){await page.screenshot({path:path.join(artifacts,'failure.png'),fullPage:true}).catch(()=>{});console.error(error);process.exitCode=1;}finally{await browser.close();}
