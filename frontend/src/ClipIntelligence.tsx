import type {Clip} from './types';

export const editorialScore=(clip:Clip)=>Math.max(0,Math.min(10,clip.editorial?.score??clip.score/10));
const label=(key:string)=>({hook:'Hook strength',completeness:'Story completeness',payoff:'Payoff & insight',audio:'Audio delivery',novelty:'Novelty & relevance',technical:'Technical quality',visual:'Visual interest',context:'Context retained',activity:'Observed activity',genre:'Content relevance'}[key]||key.replaceAll('_',' '));
const genreLabel=(key:string)=>({auto:'Automatic',general:'General / mixed',podcast:'Podcast / interview',gaming:'Gameplay',cinematic:'Cinematic',education:'Education',sports:'Sports',comedy:'Comedy',visual:'Visual content'}[key]||key.replaceAll('_',' '));
const signalTime=(seconds:number)=>`${Math.floor(seconds/60)}:${String(Math.floor(seconds%60)).padStart(2,'0')}`;

export function ClipIntelligence({clip}:{clip:Clip}){
 const info=clip.editorial;
 const intelligence=clip.intelligence;
 const genre=intelligence?.genre;
 const visual=intelligence?.basis==='visual';
 const confidence=genre?Math.max(0,Math.min(1,genre.confidence)):0;
 const estimateStrength=confidence>=.7?'Stronger evidence':confidence>=.4?'Some evidence':'Limited evidence';
 return <>
  <div className="clip-intelligence-meta">
   {clip.analysis_active===false&&<span className="previous-selection-tag">Previous selection</span>}
   <span>{info?.topic||'Source moment'}</span>
   {intelligence&&<span className="evidence-basis">{visual?'Visual moment':intelligence.basis==='mixed'?'Speech + visual evidence':'Spoken moment'}</span>}
   {info?.completeness&&<span className={`story-state ${info.completeness==='complete'&&!visual?'complete':''}`}>{visual?'Review visual context':info.completeness==='complete'?'Story checks passed':'Review context'}</span>}
  </div>
  {!!info?.tags?.length&&<div className="clip-tags">{info.tags.slice(0,5).map(tag=><span key={tag}>{tag}</span>)}</div>}
  <details className="score-reason">
   <summary>Why this moment?</summary>
   <p>{info?.explanation||clip.score_explanation}</p>
   {info?.audience&&<p><strong>Audience:</strong> {info.audience}</p>}
   {intelligence&&<section className="source-evidence" aria-label="Source evidence">
    <div className="evidence-heading"><strong>Observed in the source</strong><span>Experimental</span></div>
    {genre&&<p className="genre-estimate"><strong>{genreLabel(genre.primary)}</strong><span>{genre.source==='user-override'?'Your content choice':`${estimateStrength} for this content type`}</span></p>}
    {genre&&(genre.labels?.length??0)>1&&<p className="secondary-genres">Also considered: {(genre.labels??[]).filter(value=>value!==genre.primary).map(genreLabel).join(', ')}</p>}
    {!!genre?.evidence?.length&&<ul className="genre-evidence">{(genre.evidence??[]).map((evidence,index)=><li key={index}>{evidence}</li>)}</ul>}
    {!!intelligence.observed_signals?.length&&<ul className="observed-signals">{(intelligence.observed_signals??[]).slice(0,6).map((signal,index)=><li key={`${signal.kind}-${signal.time}-${index}`}><time>{signalTime(signal.time)}</time><span><strong>{label(signal.kind)}</strong>{signal.description}</span></li>)}</ul>}
    {intelligence.boundary_note&&<p className="boundary-note">{intelligence.boundary_note}</p>}
    {visual&&<p className="visual-caption-note">This suggestion does not depend on spoken dialogue. Review the action and its surrounding context in the source.</p>}
    {genre?.source==='automatic'&&<small>Content confidence describes the available evidence; it does not measure event accuracy.</small>}
   </section>}
   {info?.dimensions&&<div className="score-breakdown">{Object.entries(info.dimensions).map(([key,dimension])=><div key={key}><div className="score-dimension-heading"><strong>{label(key)}</strong><span>{Number(dimension.score).toFixed(1)} / 10</span></div><meter min={0} max={10} value={dimension.score} aria-label={`${label(key)} score`}/><p>{Array.isArray(dimension.evidence)?dimension.evidence.join(' '):dimension.evidence}</p></div>)}</div>}
   {!!(info?.review_indicators?.length||intelligence?.limitations?.length)&&<div className="editorial-review"><strong>Review before sharing</strong><ul>{[...new Set([...(info?.review_indicators??[]),...(intelligence?.limitations??[])])].map((indicator,index)=><li key={index}>{indicator}</li>)}</ul></div>}
   {info&&<small className="score-method">{info.method} · {info.version}{!visual&&info.confidence!=null?` · ASR word confidence ${typeof info.confidence==='number'?`${Math.round(info.confidence*100)}%`:info.confidence}`:''}</small>}
  </details>
 </>
}
