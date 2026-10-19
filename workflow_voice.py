"""Explicit native command routing; each voice/render step binds a saved job/hash."""
import json,os,re,uuid
from pathlib import Path

def route(d,text):
    from conversation_style import roman_display
    low=roman_display(text).lower().strip(' .!?')
    approvals=('haan','ha','haa','yes','ji haan','approve','approve voice','approve render')
    if getattr(d,'workflow_pending',None):
        if low in ('no','nahi','cancel'):
            d.workflow_pending=None;d.post('Zoya','Ji boss, approval cancel kar di.');return True
        if low in approvals:
            kind,job_id,key=d.workflow_pending;d.workflow_pending=None
            from promo_workflow import approve_voice,approve_render
            if kind=='voice':
                job=approve_voice(job_id,key);plan=json.loads(job['render_plan']);d.workflow_pending=('render',job_id,plan['hash'])
                d.post('Zoya','Ji boss, voiceover generate ho gaya. Local brand-card preview render karna hai? Haan boliye. Upload locked hai.')
            else:
                job=approve_render(job_id,key);d.post('Zoya','Ji boss, local video render complete hai. File: '+str(Path(__file__).parent/'workspaces/Zoya/promo'/job_id/'promo-preview.mp4')+'. Upload nahi hua.')
            return True
        d.post('Zoya','Ji boss, saved proposal ke liye haan ya cancel boliye.');return True
    if low in ('hey zoya','hello zoya','hi zoya','zoya','he zoya','helo zoya','हे जोया'):
        answer='Ji boss, bataiye, main aapki kya madad kar sakti hoon?';d.post('Zoya',answer)
        cache=Path(__file__).parent/'data/greetings/wake.mp3'
        if cache.exists():d.play_mp3(cache)
        else:d.say(answer)
        return True
    if any(w in low for w in ('promo','voiceover','voice over','pet care','pet-care','पेट केयर')):
        import local_settings,budget_guard
        root=Path(__file__).parent;local_settings.load(root);budget_guard.load_policy(root)
        from promo_workflow import create
        job=create(text,str(uuid.uuid4()));plan=json.loads(job['voice_plan']);d.workflow_pending=('voice',job['id'],plan['hash'])
        d.post('Script Agent',job['script'])
        d.post('Niche / Claims Agent','Generic brand demo only. No product claims or viral-demand evidence invented.')
        d.post('Zoya','Ji boss, script ready hai. Is exact script ka ElevenLabs voiceover generate karna hai? Haan boliye. Rendering ke liye alag approval hogi.')
        return True
    if any(w in low for w in ('niche','viral','reverse engineering','reverse-engineering')):
        d.pending=('task',text+'\nRequired: dated competitor evidence, sources, missing-input blockers. No publishing, purchases or logins. Use enabled research/subagent tools; do not claim execution without tool results.')
        d.post('Zoya','Ji boss, dated niche research Skales ko dene ka plan hai. Missing evidence ko guess nahi karenge. Is task ko approve karna hai? Haan boliye.')
        return True
    return False
