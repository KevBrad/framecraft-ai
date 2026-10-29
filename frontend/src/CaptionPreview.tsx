import {useEffect,useState} from 'react';
import type {Caption,Edit} from './types';
export const modernPresets=[['creator-bold','Creator Bold'],['karaoke-pop','Karaoke Pop'],['clean-editorial','Clean Editorial'],['podcast-dynamic','Podcast Dynamic'],['viral-energy','Viral Energy']] as const;
export const captionEmojis=[['','None'],['💰','Money'],['💡','Idea'],['🔥','Energy'],['😮','Surprise'],['🎯','Goal'],['📈','Growth'],['❤️','Heart'],['🎉','Celebration']] as const;
export function presetEdit(preset:Edit['caption_preset']):Partial<Edit>{
 const common:Partial<Edit>={caption_preset:preset,caption_x:.5,caption_y:.79};
 const values:Partial<Record<Edit['caption_preset'],Partial<Edit>>>={
  'creator-bold':{font_size:56,text_color:'#FFFFFF',highlight_color:'#FFE166',stroke:3,shadow:1,keyword_highlights:true,animated_captions:true},
  'karaoke-pop':{font_size:54,text_color:'#FFFFFF',highlight_color:'#A8EF66',stroke:3,shadow:1,keyword_highlights:true,animated_captions:true},
  'clean-editorial':{font_size:40,text_color:'#FFFFFF',highlight_color:'#D7E3C8',stroke:1,shadow:2,keyword_highlights:false,animated_captions:false},
  'podcast-dynamic':{font_size:48,text_color:'#FFFFFF',highlight_color:'#80D9EC',stroke:2,shadow:2,keyword_highlights:true,animated_captions:true},
  'viral-energy':{font_size:58,text_color:'#FFFFFF',highlight_color:'#FFE166',stroke:3,shadow:2,keyword_highlights:true,animated_captions:true,contextual_emojis:true}
 };
 return {...common,...values[preset]};
}
export function CaptionPreview({caption,edit,current}:{caption:Caption;edit:Edit;current:number}){
 const aligned=!!caption.words?.length;
 const words=aligned?caption.words!:caption.text.trim().split(/\s+/).map((word,index,all)=>({word,start:caption.start+(caption.end-caption.start)*index/all.length,end:caption.start+(caption.end-caption.start)*(index+1)/all.length}));
 const keywords=new Set((caption.keywords??[]).map(word=>word.toLocaleLowerCase()));
 const karaoke=edit.caption_preset==='karaoke'||edit.caption_preset==='karaoke-pop'||edit.caption_preset==='viral-energy';
 return <>{edit.contextual_emojis&&edit.emoji_frequency!=='off'&&caption.emoji_enabled!==false&&caption.emoji&&<span className="caption-emoji-preview" aria-label="Caption emoji">{caption.emoji}</span>}{words.map((word,index)=><span key={index} className={`${edit.keyword_highlights&&keywords.has(word.word.toLocaleLowerCase().replace(/[^\p{L}\p{N}]/gu,''))?'keyword ':''}${aligned&&edit.animated_captions&&karaoke&&current>=word.start&&current<word.end?'spoken':''}`}>{word.word}{index<words.length-1?' ':''}</span>)}</>
}

export function CaptionKeywords({caption,index,onChange}:{caption:Caption;index:number;onChange:(keywords:string[])=>void}){
 const serialized=(caption.keywords??[]).join(', '),[text,setText]=useState(serialized);
 useEffect(()=>{const normalized=text.split(',').map(word=>word.trim()).filter(Boolean).join(', ');if(normalized!==serialized)setText(serialized)},[serialized]);
 return <input aria-label={`Caption ${index+1} keywords`} value={text} onChange={event=>{setText(event.target.value);onChange(event.target.value.split(',').map(word=>word.trim()).filter(Boolean).slice(0,12))}} placeholder="Comma-separated words" maxLength={240}/>;
}
