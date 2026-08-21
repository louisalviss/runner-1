#!/usr/bin/env python3
import json, math, time
from datetime import datetime, timezone
from urllib.parse import urlencode
from urllib.request import Request, urlopen

BASE='https://fapi.binance.com'
INTERVAL_MS=5*60*1000
STATE_START=1785171600000
W1=(1785862800000,1786294800000)
W2=(1786294800000,1786813200000)
TEST_END=1787356800000
SYMBOLS=['BNBUSDT','TRXUSDT','SOLUSDT','ETHUSDT','XRPUSDT']
BENCH='BTCUSDT'
EXPECTED={('BNBUSDT','W1'):(5,3.420895522),('TRXUSDT','W1'):(4,2.60),('BNBUSDT','W2'):(3,6.90),('TRXUSDT','W2'):(2,-2.00)}

def get_json(path, params=None, retries=4):
    url=BASE+path
    if params: url += '?' + urlencode(params)
    last=None
    for k in range(retries):
        try:
            req=Request(url, headers={'User-Agent':'wr-rsrw-research/1.0'})
            with urlopen(req, timeout=30) as r: return json.loads(r.read().decode())
        except Exception as e:
            last=e; time.sleep(1.5*(k+1))
    raise last

def fetch_klines(symbol, start_ms, end_ms):
    rows=[]; cur=start_ms
    while cur < end_ms:
        data=get_json('/fapi/v1/klines', {'symbol':symbol,'interval':'5m','startTime':cur,'endTime':end_ms-1,'limit':1500})
        if not data: break
        for x in data:
            ot=int(x[0])
            if ot>=end_ms: break
            rows.append({'t':ot,'o':float(x[1]),'h':float(x[2]),'l':float(x[3]),'c':float(x[4])})
        nxt=int(data[-1][0])+INTERVAL_MS
        if nxt<=cur: break
        cur=nxt; time.sleep(0.03)
    d={r['t']:r for r in rows}; return [d[t] for t in sorted(d)]

def exchange_ticks():
    info=get_json('/fapi/v1/exchangeInfo'); out={}
    for s in info['symbols']:
        if s['symbol'] in SYMBOLS+[BENCH]:
            for f in s['filters']:
                if f.get('filterType')=='PRICE_FILTER': out[s['symbol']]=float(f['tickSize'])
    return out

def ema(vals,n):
    a=2/(n+1); out=[]; prev=None
    for v in vals:
        if v is None: out.append(None); continue
        prev=v if prev is None else a*v+(1-a)*prev; out.append(prev)
    return out

def rma(vals,n):
    out=[None]*len(vals); buf=[]; prev=None
    for i,v in enumerate(vals):
        if v is None: buf=[]; prev=None; continue
        if prev is None:
            buf.append(v)
            if len(buf)==n: prev=sum(buf)/n; out[i]=prev
        else: prev=(prev*(n-1)+v)/n; out[i]=prev
    return out

def rolling_sum(vals,n):
    out=[None]*len(vals); q=[]; s=0.0
    for i,v in enumerate(vals):
        if v is None: q=[]; s=0.0; continue
        q.append(v); s+=v
        if len(q)>n: s-=q.pop(0)
        if len(q)==n: out[i]=s
    return out

def rolling_ext(vals,n,ismax=True):
    out=[None]*len(vals)
    for i in range(len(vals)):
        if i+1>=n:
            w=vals[i-n+1:i+1]
            if all(v is not None for v in w): out[i]=(max(w) if ismax else min(w))
    return out

def stats(trades):
    rs=[x['R'] for x in trades]; n=len(rs); total=sum(rs); gp=sum(x for x in rs if x>0); gl=-sum(x for x in rs if x<0); streak=mx=0
    for x in rs:
        if x<0: streak+=1; mx=max(mx,streak)
        else: streak=0
    return {'trades':n,'total_R':round(total,6),'avg_R':round(total/n,6) if n else None,'win_rate_pct':round(100*sum(x>0 for x in rs)/n,2) if n else None,'PF_R':round(gp/gl,4) if gl>0 else (None if gp==0 else 999.0),'max_L_streak':mx}

def bar_path(bar):
    o,h,l,c=bar['o'],bar['h'],bar['l'],bar['c']; return [o,h,l,c] if abs(o-h)<abs(o-l) else [o,l,h,c]
def crossed(a,b,x): return min(a,b)<=x<=max(a,b)

def exit_from_path(path,direction,stop,target,bar):
    chosen=None; p0=path[0]
    if direction==1:
        if p0<=stop: chosen='SL'
        elif p0>=target: chosen='TP'
    else:
        if p0>=stop: chosen='SL'
        elif p0<=target: chosen='TP'
    if chosen is None:
        for a,b in zip(path,path[1:]):
            levels=[]
            if crossed(a,b,stop): levels.append(('SL',stop))
            if crossed(a,b,target): levels.append(('TP',target))
            if levels:
                chosen=(min(levels,key=lambda z:z[1])[0] if b>=a else max(levels,key=lambda z:z[1])[0]); break
    if chosen is not None and bar['h']>=max(stop,target) and bar['l']<=min(stop,target): return 'AMBIG'
    return chosen

def after_entry_exit(bar,direction,entry,stop,target):
    path=bar_path(bar)
    if (direction==1 and path[0]>=entry) or (direction==-1 and path[0]<=entry): return exit_from_path(path,direction,stop,target,bar)
    for j in range(len(path)-1):
        a,b=path[j],path[j+1]
        trigger=(direction==1 and b>=a and crossed(a,b,entry)) or (direction==-1 and b<=a and crossed(a,b,entry))
        if trigger: return exit_from_path([entry,b]+path[j+2:],direction,stop,target,bar)
    return None

def active_bracket_exit(bar,direction,stop,target):
    if bar['h']>=max(stop,target) and bar['l']<=min(stop,target): return 'AMBIG'
    return exit_from_path(bar_path(bar),direction,stop,target,bar)

def simulate(symbol,bars,tick,bench_map,filter_on=False):
    O=[b['o'] for b in bars]; H=[b['h'] for b in bars]; L=[b['l'] for b in bars]; C=[b['c'] for b in bars]
    E=ema(C,21); E9=ema(C,9); TR=[]
    for i,b in enumerate(bars): TR.append(b['h']-b['l'] if i==0 else max(b['h']-b['l'],abs(b['h']-C[i-1]),abs(b['l']-C[i-1])))
    atr10=rma(TR,10); atr14=rma(TR,14); trs=rolling_sum(TR,14); hh=rolling_ext(H,14,True); ll=rolling_ext(L,14,False)
    resistance=support=None; above=below=0; pending=None; active=None; trades=[]
    for i,b in enumerate(bars):
        t=b['t']; tc=t+INTERVAL_MS; closed_this_bar=False
        if i>=21:
            j=i-11; winH=H[i-21:i]; winL=L[i-21:i]
            ph=H[j] if H[j]>=max(winH) else None; pl=L[j] if L[j]<=min(winL) else None
            if ph is not None and (resistance is None or ph!=resistance): resistance=ph
            if pl is not None and (support is None or pl!=support): support=pl
        below=below+1 if E[i] is not None and C[i]<E[i] else 0; above=above+1 if E[i] is not None and C[i]>E[i] else 0
        high_above=above>=12; high_below=below>=12; ema_up=i>=2 and E[i] is not None and E[i-2] is not None and E[i]>=E[i-2]
        ang=180/math.pi*math.atan((E[i]-E[i-4])/atr10[i]/4) if i>=4 and atr10[i] not in (None,0) and E[i] is not None and E[i-4] is not None else None
        pang=180/math.pi*math.atan((E[i-1]-E[i-5])/atr10[i-1]/4) if i>=5 and atr10[i-1] not in (None,0) and E[i-1] is not None and E[i-5] is not None else None
        outside=ang is not None and (ang>5 or ang<-5); angle_green=ang is not None and pang is not None and ang>pang and outside; angle_red=ang is not None and pang is not None and ang<pang and outside
        chop=100*math.log10(trs[i]/(hh[i]-ll[i]))/math.log10(14) if trs[i] is not None and hh[i] is not None and ll[i] is not None and hh[i]>ll[i] else None
        chop_pass=chop is not None and chop<50; range_pass=atr14[i] not in (None,0) and (H[i]-L[i])/atr14[i]<=1.5
        if pending is not None:
            if i==pending['signal_i']+1:
                d=pending['dir']; cond=(d==1 and H[i]>=pending['entry']) or (d==-1 and L[i]<=pending['entry'])
                if cond:
                    kind=after_entry_exit(b,d,pending['entry'],pending['stop'],pending['target']); active=dict(pending); active['entry_i']=i; pending=None
                    if kind is not None:
                        active['R']=-1.0 if kind in ('SL','AMBIG') else 2.3; active['exit_kind']=kind; active['exit_t']=tc; trades.append(active); active=None; closed_this_bar=True
            elif i>pending['signal_i']+1: pending=None
        just_entered=active is not None and active.get('entry_i')==i
        if active is not None and not just_entered:
            kind=active_bracket_exit(b,active['dir'],active['stop'],active['target'])
            if kind:
                active['R']=-1.0 if kind in ('SL','AMBIG') else 2.3; active['exit_kind']=kind; active['exit_t']=tc; trades.append(active); active=None; closed_this_bar=True
        if active is not None:
            dt=datetime.fromtimestamp(tc/1000,timezone.utc); mins=dt.hour*60+dt.minute; session_exit=(mins>=23*60+40 or mins==0)
            long_ema=active['dir']==1 and C[i]<E[i] and not high_above and not ema_up; short_ema=active['dir']==-1 and C[i]>E[i] and not high_below and ema_up
            if session_exit or long_ema or short_ema:
                d=active['dir']; active['R']=d*(C[i]-active['entry'])/abs(active['entry']-active['stop']); active['exit_kind']='SESSION' if session_exit else 'EMA'; active['exit_t']=tc; trades.append(active); active=None; closed_this_bar=True
        if pending is not None and i>=pending['signal_i']+1: pending=None
        dt=datetime.fromtimestamp(tc/1000,timezone.utc); mins=dt.hour*60+dt.minute; session_allowed=not (mins>=23*60+15 or mins==0)
        flat_free=active is None and pending is None and not closed_this_bar; long_ready=high_above and C[i]>E[i] and angle_green and chop_pass and resistance is not None; short_ready=high_below and C[i]<E[i] and angle_red and chop_pass and support is not None
        bc,bm=bench_map.get(t,(None,None)); rs=bc is not None and bm is not None and C[i]>E[i] and C[i]>E9[i] and bc<bm; rw=bc is not None and bm is not None and C[i]<E[i] and C[i]<E9[i] and bc>bm
        lp=(not filter_on) or rs; sp=(not filter_on) or rw
        newL=flat_free and session_allowed and range_pass and lp and C[i]>O[i] and long_ready and C[i]>resistance and L[i]<=resistance
        newS=flat_free and session_allowed and range_pass and sp and C[i]<O[i] and short_ready and C[i]<support and H[i]>=support
        if newL:
            e=H[i]+tick; s=L[i]-tick; pending={'symbol':symbol,'dir':1,'signal_i':i,'signal_t':tc,'entry':e,'stop':s,'target':e+2.3*(e-s),'rs':rs,'rw':rw}
        elif newS:
            e=L[i]-tick; s=H[i]+tick; pending={'symbol':symbol,'dir':-1,'signal_i':i,'signal_t':tc,'entry':e,'stop':s,'target':e-2.3*(s-e),'rs':rs,'rw':rw}
    return trades

def select_window(trades,w): return [x for x in trades if w[0]<=x['signal_t']<w[1]]
def main():
    ticks=exchange_ticks(); bench_bars=fetch_klines(BENCH,STATE_START-7*86400000,TEST_END); be=ema([b['c'] for b in bench_bars],21); bench_map={b['t']:(b['c'],be[i]) for i,b in enumerate(bench_bars)}
    result={'generated_at':datetime.now(timezone.utc).isoformat(),'benchmark':BENCH,'state_start':STATE_START,'test_end':TEST_END,'symbols':{},'oracle':{}}; all_base=[]; all_filt=[]
    for sym in SYMBOLS:
        bars=fetch_klines(sym,STATE_START,TEST_END); gaps=sum(1 for a,b in zip(bars,bars[1:]) if b['t']-a['t']!=INTERVAL_MS); base=simulate(sym,bars,ticks[sym],bench_map,False); filt=simulate(sym,bars,ticks[sym],bench_map,True); all_base+=base; all_filt+=filt
        aligned=[x for x in base if (x['dir']==1 and x['rs']) or (x['dir']==-1 and x['rw'])]; non=[x for x in base if x not in aligned]
        result['symbols'][sym]={'bars':len(bars),'gaps':gaps,'baseline_full':stats(base),'filter_full':stats(filt),'tag_aligned':stats(aligned),'tag_not_aligned':stats(non),'W1_base':stats(select_window(base,W1)),'W1_filter':stats(select_window(filt,W1)),'W2_base':stats(select_window(base,W2)),'W2_filter':stats(select_window(filt,W2))}
    ok=True
    for (sym,wn),(en,er) in EXPECTED.items():
        got=result['symbols'][sym][f'{wn}_base']; p=got['trades']==en and abs(got['total_R']-er)<=0.03; ok &= p; result['oracle'][f'{sym}_{wn}']={'expected_trades':en,'expected_R':er,'got':got,'pass':p}
    result['oracle']['PASS']=ok; aligned=[x for x in all_base if (x['dir']==1 and x['rs']) or (x['dir']==-1 and x['rw'])]
    result['aggregate']={'baseline_full':stats(all_base),'filter_full':stats(all_filt),'tag_aligned':stats(aligned),'tag_not_aligned':stats([x for x in all_base if x not in aligned])}
    open('research/rsrw_result.json','w').write(json.dumps(result,indent=2,sort_keys=True)); print(json.dumps(result,indent=2,sort_keys=True))
if __name__=='__main__': main()
