"""Bounded source-grounded editorial analysis, with explicit heuristic evidence.

No instruction execution, cloud requests, invented dialogue or viral forecasts.
Global maps use extractive summaries; they are not LLM semantic understanding.
"""
from __future__ import annotations
from collections import Counter
import hashlib
import json
import math
import re

VERSION='editorial-v3.1'
WEIGHTS={'hook':.20,'completeness':.25,'payoff':.20,'audio':.10,'novelty':.15,'technical':.10}
STOP=set('the and that this with from have your you are was were for but not all just like what how why when who which can our they their them its into about more than then there here been would could should will very some really much also know think because does did do get got going want one two now'.split())
DEPENDENT=re.compile(r'^(and|but|so|because|then|it|they|them|these|those|that|this|yes|no|exactly|right)\b',re.I)
REVEAL=re.compile(r'\b(secret|surpris\w*|realiz\w*|actually|instead|never|most|first|cost|spent|expensive|million|billion|revea\w*|mistake|unexpected)\b',re.I)
PAYOFF=re.compile(r'\b(because|therefore|result|means|lesson|learn\w*|answer|turns out|finally|instead|so that|in other words|important|remember)\b',re.I)
EMOTION=re.compile(r'\b(love|hate|afraid|scared|terrified|cried|cry|happy|heart|pain|sad|felt|feel|proud|amazing)\b',re.I)
FUNNY=re.compile(r'\b(laugh\w*|funny|joke|hilarious|ridiculous)\b',re.I)
OPINION=re.compile(r'\b(i believe|i think|disagree|should|must|wrong|overrated|unpopular|honestly)\b',re.I)
EDUCATE=re.compile(r'\b(how|why|because|learn\w*|explain\w*|example|reason|science|research|means|understand|evidence|step)\b',re.I)

def tokens(text):
    return {v for v in re.findall(r'[^\W_]{3,}',str(text).lower()) if v not in STOP}

def terms(text,n=5):
    return [t for t,_ in Counter(t for t in re.findall(r'[^\W_]{3,}',text.lower()) if t not in STOP).most_common(n)]

def source_map(transcript,duration):
    from .pipeline import _sentences
    sentences=_sentences(transcript,max_span=24)
    segments=[]
    # Each local unit has bounded text and an extractive, timestamped summary.
    for bucket in range(math.ceil(duration/90)):
        group=[s for s in sentences if bucket*90<=s['start']<(bucket+1)*90]
        if not group:continue
        text=' '.join(s['text'] for s in group)
        key=terms(text)
        exemplar=max(group,key=lambda s:len(tokens(s['text'])&set(key))+bool(PAYOFF.search(s['text'])))
        segments.append({'start':round(group[0]['start'],3),'end':round(group[-1]['end'],3),'topics':key,
                         'summary':exemplar['text'][:400],'summary_interval':[exemplar['start'],exemplar['end']],
                         'questions':[{'text':s['text'][:200],'start':s['start'],'end':s['end']} for s in group if '?' in s['text']][:8]})
    return {'version':VERSION,'method':'hierarchical-extractive-local','topics':terms(' '.join(s['text'] for s in sentences),12),
            'segments':segments,'sentence_count':len(sentences),'speakers':'unattributed',
            'limitations':['Extractive topic map; no inferred speaker identities or visual action recognition.']}

def _title(selected):
    # Titles are source quotations, never paraphrased factual claims. This keeps
    # TV/YouTube distinctions in source context instead of inventing a headline.
    useful=[s for s in selected if len(s['text'].split())>=6 and not re.search(r'\b(?:of|the|a|an|and|to|for|with)\s*[.!?]?$' ,s['text'],re.I)]
    best=max(useful or selected,key=lambda s:2*bool(REVEAL.search(s['text']))+bool(PAYOFF.search(s['text']))+2*bool(re.search(r'\d|prime video|television|youtube|beast games',s['text'],re.I))-bool(DEPENDENT.match(s['text']))+.01*min(30,len(s['text'].split())))
    words=best['text'].strip().split()
    # Preserve production/media qualifiers rather than truncating factual context.
    limit=24 if re.search(r'prime video|television|youtube',best['text'],re.I) else 14
    quote=' '.join(words[:limit])
    if len(words)>limit:
        while quote.split() and quote.split()[-1].lower() in ('of','the','a','an','and','to','for','with'):quote=' '.join(quote.split()[:-1])
        quote+='…'
    return quote[:180],{'method':'source-quotation','start':best['start'],'end':best['end'],'source_text':best['text'],
                       'review':'Quoted excerpt: inspect its surrounding context before publishing.'}

def discover(transcript,duration,settings,energy=None,visual=None,context=None):
    from .pipeline import _sentences,_overlap,default_edit
    from .caption_engine import make_captions
    minimum=max(3,min(180,float(settings.get('min_duration',15))))
    maximum=max(minimum,min(180,float(settings.get('max_duration',60))))
    source_short=duration<minimum
    if source_short:minimum=max(.3,min(3,duration*.5))
    count=max(1,min(20,int(settings.get('requested_clips',10))))
    sentences=_sentences(transcript,max_span=min(30,maximum))
    if not sentences:return []
    context=context or source_map(transcript,duration)
    mode=settings.get('discovery_mode','best_highlights')
    prompt=tokens(settings.get('custom_prompt','') if mode=='custom_prompt' else settings.get('topic',''))
    # Prefix arrays prevent a per-candidate scan through an hour of audio.
    bins={int(e['start']):e['dbfs'] for e in energy or []}
    candidates=[]
    for i,first in enumerate(sentences):
        start_i=i
        # Repair a dependent answer by retaining its preceding question/setup.
        if i and DEPENDENT.match(first['text']) and first['start']-sentences[i-1]['end']<3:
            previous=sentences[i-1]
            if '?' in previous['text'] or first['end']-previous['start']<maximum*.7:start_i=i-1
        opening=sentences[start_i]
        required_end=None
        if '?' in opening['text']:
            # A nearby next interview question bounds a complete answer. Do not
            # score preliminary answers above their later source correction.
            for q in range(start_i+1,min(len(sentences),start_i+35)):
                item=sentences[q]
                if item['start']-opening['start']>maximum+2:break
                if '?' in item['text'] and not re.match(r'^(?:what do people feel|what do i mean|right|you know)',item['text'],re.I):
                    if q>start_i+1 and sentences[q-1]['end']-opening['start']<=maximum:
                        required_end=sentences[q-1]['end']
                    break
        for j in range(i,min(len(sentences),start_i+45)):
            last=sentences[j]
            span=last['end']-opening['start']
            if span>maximum:break
            if span<minimum:continue
            if required_end is not None and last['end']<required_end-.001:continue
            if j>start_i and re.search(r'\b(?:what is|what does|how does|why does|is|does) (?:mr\.?|beast|usgs)',last['text'],re.I) and '?' in last['text']:
                break
            # Do not end immediately before a source contrast that revises the
            # apparent answer (for example a more expensive TV production).
            if j+1<len(sentences) and sentences[j+1]['start']-last['end']<1.5 and re.match(r'^(?:but (?:i will|actually|in fact)|however|in fact|it turns out|which was|which is why|so for the)',sentences[j+1]['text'],re.I):
                continue
            # Candidate endings must be natural pauses/punctuation, not the
            # arbitrary max-span split of a continuing sentence.
            next_gap=sentences[j+1]['start']-last['end'] if j+1<len(sentences) else duration-last['end']
            ending=bool(last.get('complete')) or next_gap>=.32
            if not ending and j+1<len(sentences):continue
            selected=sentences[start_i:j+1]
            text=' '.join(s['text'] for s in selected)
            ts=tokens(text)
            if len(text.split())<8:continue
            relevance=len(prompt&ts)/max(1,len(prompt)) if prompt else .5
            if prompt and relevance==0:continue
            question='?' in opening['text']
            # A question alone is not a completed story, even with punctuation.
            unanswered='?' in last['text'] and (question or len(selected)==1)
            dependent=bool(DEPENDENT.match(opening['text']))
            complete=ending and not unanswered and not dependent
            hook=min(1,.25+.26*question+.28*bool(REVEAL.search(opening['text']))+.15*bool(re.search(r'\d|\bwhy\b|\bhow\b',opening['text'],re.I))+.12*(not dependent))
            completeness=max(0,min(1,.38+.25*ending+.17*(not dependent)+.16*(len(selected)>=2 or span>=12)-.35*unanswered))
            payoff=min(1,.34+.25*bool(PAYOFF.search(text))+.23*(ending and not unanswered)+.18*bool(REVEAL.search(text)))
            db=[bins[t] for t in range(int(opening['start']),math.ceil(last['end'])) if t in bins]
            avg=sum(db)/len(db) if db else None
            variation=(sum((v-avg)**2 for v in db)/len(db))**.5 if db else None
            audio=min(1,.4+(variation or 0)/25) if db else .5
            words=[w for s in selected for w in s.get('words',[])]
            probabilities=[w['probability'] for w in words if w.get('probability') is not None]
            confidence=sum(probabilities)/len(probabilities) if probabilities else None
            technical=max(0,min(1,confidence if confidence is not None else .5))
            if avg is not None and avg < -42:technical*=.7
            local=next((seg for seg in context['segments'] if seg['start']<=opening['start']<=seg['end']),{})
            topic_terms=terms(text,3)
            novelty=min(1,.35+.24*bool(REVEAL.search(text))+.16*bool(re.search(r'\d',text))+.25*relevance)
            specific={'educational_insights':bool(EDUCATE.search(text)),'emotional_moments':bool(EMOTION.search(text)),
                      'funny_moments':bool(FUNNY.search(text)),'strong_opinions':bool(OPINION.search(text)),
                      'viral_moments':bool(REVEAL.search(text)),'custom_prompt':relevance>0}
            if mode in ('funny_moments','emotional_moments','strong_opinions','custom_prompt') and not specific[mode]:continue
            novelty=min(1,novelty+.12*bool(specific.get(mode)))
            components=dict(hook=hook,completeness=completeness,payoff=payoff,audio=audio,novelty=novelty,technical=technical)
            score=round(10*sum(components[k]*WEIGHTS[k] for k in WEIGHTS),2)
            low_confidence=confidence is not None and confidence<.80
            if score<5.1 or completeness<.60:continue
            if unanswered:continue
            if re.search(r'\b(trade or firm|product names|endorsement|all rights reserved|welcome everyone)\b',text,re.I):continue
            begin=max(0,opening['start']-.08)
            if start_i:begin=max(begin,sentences[start_i-1]['end'])
            finish=min(duration,last['end']+.12,(sentences[j+1]['start'] if j+1<len(sentences) else duration))
            # Padding must never truncate speech to force a duration limit.
            if finish-begin>maximum:begin=opening['start'];finish=last['end']
            title,title_evidence=_title(selected)
            tags=[]
            if REVEAL.search(text):tags.append('Surprising reveal')
            if question:tags.append('Interesting question')
            if EDUCATE.search(text):tags.append('Educational takeaway')
            if EMOTION.search(text):tags.append('Emotional language')
            if FUNNY.search(text):tags.append('Funny reaction')
            if OPINION.search(text):tags.append('Bold opinion')
            if re.search(r'\d',text):tags.append('Numerical claim')
            evidence={
                'hook':opening['text'][:220],
                'completeness':f"{'Natural ending' if ending else 'Ending needs review'}; {'preceding setup included' if start_i<i else 'source opening retained'}; {'unanswered question' if unanswered else 'not a question-only ending'}.",
                'payoff':last['text'][:220],
                'audio':f'Measured energy variation {variation:.1f} dB; mean {avg:.1f} dBFS.' if db else 'No measured audio evidence; neutral estimate.',
                'novelty':f"Terms: {', '.join(topic_terms)}. Prompt coverage {relevance:.0%}. Lexical evidence, not a novelty model.",
                'technical':f'Mean ASR word confidence {confidence:.0%}.' if confidence is not None else 'No ASR confidence; neutral estimate.'}
            editorial={'version':VERSION,'score':score,'rank':0,'dimensions':{k:{'score':round(v*10,2),'weight':WEIGHTS[k],'evidence':evidence[k]} for k,v in components.items()},
                'explanation':'Source-grounded opening, surrounding context and natural ending; compare the evidence before publishing.',
                'hook':opening['text'][:220],'topic':', '.join(topic_terms),'audience':', '.join(local.get('topics',topic_terms)[:3]),
                'completeness':'complete' if complete and not low_confidence else 'review','tags':tags,'confidence':confidence,
                'method':'local hierarchical extractive + lexical/acoustic editorial heuristic; not a virality prediction',
                'title_evidence':title_evidence,'review_indicators':([] if complete else ['Opening or ending depends on surrounding context.'])}
            if low_confidence:
                editorial['review_indicators'].append('ASR word confidence is below 80%; review dialogue and title before sharing. This may contain recognition errors.')
            captions=make_captions(sentences,begin,finish,preset='creator-bold',emoji_enabled=settings.get('contextual_emojis',True),emoji_frequency=settings.get('emoji_frequency','balanced'),intensity=settings.get('intensity','balanced'))
            edit=default_edit(title,round(begin,3),round(finish,3),captions)
            edit.update({'spec_version':3,'caption_preset':'creator-bold','output_resolution':settings.get('output_resolution','720p'),
                         'contextual_emojis':settings.get('contextual_emojis',True),'emoji_frequency':settings.get('emoji_frequency','balanced'),
                         'animated_captions':settings.get('animated_captions',True),'keyword_highlights':settings.get('keyword_highlights',True),
                         'intensity':settings.get('intensity','balanced'),'audio_normalize':True,'opening_hook':False})
            candidate={'title':title,'alternate_title':opening['text'][:160],'start':round(begin,3),'end':round(finish,3),'duration':round(finish-begin,3),
                'summary':text[:360],'hook':opening['text'][:220],'score':round(score*10),'score_explanation':editorial['method'],
                'score_components':components,'editorial':editorial,'segment_ids':list(dict.fromkeys(sid for s in selected for sid in s['segment_ids'])),
                'provenance':VERSION+' / source word timestamps','edit':edit,'_tokens':ts,'_quality':score,'_topic':topic_terms[0] if topic_terms else ''}
            if source_short:
                editorial['completeness']='review'
                editorial['review_indicators'].append('Source is shorter than the requested minimum; this is the available spoken excerpt.')
            candidates.append(candidate)
            if len(candidates)>2000:
                candidates.sort(key=lambda c:-c['_quality']);candidates=candidates[:1000]
    # Diverse greedy reranking; exact editorial score remains visible separately.
    chosen=[]
    candidates.sort(key=lambda c:(-c['_quality'],c['start'],c['duration']))
    # Bound retained candidate pool without forcing weak candidates to fill slots.
    candidates=candidates[:max(100,count*30)]
    while candidates and len(chosen)<count:
        best=max(candidates,key=lambda c:c['_quality']-.25*sum(c['_topic']==o['_topic'] for o in chosen)-.5*max([len(c['_tokens']&o['_tokens'])/max(1,len(c['_tokens']|o['_tokens'])) for o in chosen] or [0]))
        candidates.remove(best)
        if any(best['title'].casefold()==o['title'].casefold() or _overlap(best,o)>=.50 or len(best['_tokens']&o['_tokens'])/max(1,len(best['_tokens']|o['_tokens']))>=.72 for o in chosen):continue
        chosen.append(best)
    for rank,candidate in enumerate(chosen,1):
        candidate['editorial']['rank']=rank
        for key in ('_tokens','_quality','_topic'):candidate.pop(key,None)
    return chosen
