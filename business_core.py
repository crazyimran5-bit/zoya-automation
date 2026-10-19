"""Ten scoped specialist roles and deterministic dependency plans. No autonomous permissions."""
import os

SPECIALISTS = {
 'Research': {'mission':'Dated market and competitor evidence; never invent live demand','skills':['research','yt-viral','yt-outlier','opportunities']},
 'Script': {'mission':'Original channel-specific scripts and sourced educational drafts','skills':['yt-script','islamic-story']},
 'Packaging': {'mission':'Titles, thumbnails, SEO and community reply drafts','skills':['yt-package','yt-seo','yt-comment']},
 'Editing': {'mission':'Transcript cut decisions, Shorts and chapters; no fabricated rendered media','skills':['yt-edit','yt-shorts','yt-chapters']},
 'Creative': {'mission':'Product-faithful creative briefs and approved offline Higgsfield handoffs','skills':['higgsfield-brief','higgsfield-plan']},
 'Monetizer': {'mission':'Audience growth, leads, retention and original-content publication checks','skills':['yt-retention','yt-audit','metrics-review','monetization-monitor','content-check']},
 'AmazonListing': {'mission':'Truthful product listing and A+ drafts from verified specifications','skills':['amazon-listing','amazon-viability']},
 'AmazonPPC': {'mission':'Report-based keyword, bid and budget proposals; no Ads writes','skills':['amazon-ppc']},
 'Finance': {'mission':'Contribution profit, cash runway, operating plan and 90-day milestones','skills':['amazon-audit','business-plan','office-draft','yt-plan','finance-review','pipeline','sprint','challenge']},
 'Resolver': {'mission':'Source/QA review, failure diagnosis and bounded recovery recommendations','skills':['yt-policy','problem-resolver','app-prototype','health','security-review']}
}

def worker_limits():
    raw=os.environ.get('ZOYA_MAX_INFLIGHT','3')
    try: n=int(raw)
    except ValueError: raise ValueError('ZOYA_MAX_INFLIGHT must be an integer from 1 to 10')
    if not 1<=n<=10: raise ValueError('ZOYA_MAX_INFLIGHT must be from 1 to 10')
    return n

def workflow_specs(channel,command):
    # Named dependencies refer to earlier steps only. Branches need separate approvals.
    if channel.startswith('YT-'):
        return [('survey','yt-outlier' if 'Research role only: return JSON matching' in command else 'research',command,[]),('script','yt-script',command,['survey']),
                ('package','yt-package',command,['script']),('edit','yt-edit',command,['script']),
                ('quality','yt-policy',command,['package','edit']),
                ('creative','higgsfield-brief',command,['quality'])]
    if channel=='Amazon':
        return [('survey','research',command,[]),('audit','amazon-audit',command,['survey']),
                ('listing','amazon-listing',command,['audit']),('ppc','amazon-ppc',command,['audit']),
                ('finance','finance-review',command,['listing','ppc']),
                ('review','problem-resolver',command,['finance'])]
    return [('survey','research',command,[]),('plan','business-plan',command,['survey']),
            ('review','problem-resolver',command,['plan'])]

def challenge_scorecard(raw):
    from datetime import date,timedelta
    import math
    required={'start_date','as_of','goal_metric','goal_usd','actual_revenue_usd','actual_contribution_profit_usd','available_cash_usd','currency'}
    if not isinstance(raw,dict) or set(raw)!=required: raise ValueError('Challenge fields must match documented schema')
    if raw['currency']!='USD' or raw['goal_metric']!='cumulative_revenue':
        raise ValueError('This scorecard defines the goal as cumulative USD revenue, not profit or valuation')
    start=date.fromisoformat(raw['start_date']);now=date.fromisoformat(raw['as_of'])
    if now<start: raise ValueError('As-of date precedes challenge start')
    for key in ('goal_usd','actual_revenue_usd','actual_contribution_profit_usd','available_cash_usd'):
        value=raw[key]
        if value is None and key!='goal_usd': continue
        if isinstance(value,bool) or not isinstance(value,(int,float)) or not math.isfinite(value): raise ValueError('Finite numeric metrics required; unknown actuals must be null')
        if key!='actual_contribution_profit_usd' and value<0: raise ValueError('Revenue, goal and cash cannot be negative')
    if raw['goal_usd']<=0: raise ValueError('Positive goal required')
    elapsed=min(90,(now-start).days+1);days_left=90-elapsed
    actual=raw['actual_revenue_usd'];gap=None if actual is None else max(0,raw['goal_usd']-actual)
    phases=[(14,'Validate demand, account/data access and contribution economics'),
            (30,'Run one approved YouTube and one Amazon pilot'),
            (60,'Optimize measured conversion, retention and contribution profit'),
            (90,'Scale only verified profitable capacity and cash-supported inventory')]
    return {'status':'SCORECARD_FROM_USER_SUPPLIED_DATA_NOT_PROVIDER_VERIFIED',
            'definition':raw['goal_metric'],'currency':'USD','start_date':start.isoformat(),
            'deadline':(start+timedelta(days=89)).isoformat(),'as_of':now.isoformat(),
            'elapsed_days':elapsed,'remaining_days':days_left,'goal_usd':raw['goal_usd'],
            'actual_revenue_usd':actual,'actual_contribution_profit_usd':raw['actual_contribution_profit_usd'],
            'available_cash_usd':raw['available_cash_usd'],'remaining_revenue_usd':gap,
            'required_revenue_per_remaining_day_usd':None if gap is None or days_left==0 else round(gap/days_left,2),
            'current_phase':next(text for limit,text in phases if elapsed<=limit),
            'missing_actuals':[k for k,v in raw.items() if v is None],
            'constraint':'Arithmetic target only, not a forecast or income guarantee. Source-backed profit/cash review and separate spending approval required.'}
