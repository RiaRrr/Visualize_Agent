"""Exercise the generated lesson in Chrome and independently check sampled geometry.

The reference equations here are evaluation-only and never sent to generation.
"""
from __future__ import annotations
import argparse
import json
import math
from pathlib import Path
from scripts.inspect_1019 import inspect


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('html',type=Path)
    parser.add_argument('--case',choices=('ellipse','parabola'),required=True)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    actions=[{'name':f'step_{i+1}','js':f"(()=>{{const b=document.querySelectorAll('#steps button');if(b[{i}])b[{i}].click();return window.__lessonDebug}})()"} for i in range(1,7)]
    actions += [{'name':'preset','js':"(()=>{let b=document.querySelector('#actions .special:not([hidden])');if(b)b.click();return window.__lessonDebug})()"}]
    for name,fraction in [('low',0),('middle',.5),('high',1)]:
        actions.append({'name':name,'js':f"(()=>{{const e=document.querySelector('input[type=range]');if(e){{e.value=Number(e.min)+(Number(e.max)-Number(e.min))*{fraction};e.dispatchEvent(new Event('input',{{bubbles:true}}))}}return window.__lessonDebug}})()"})
    actions.append({'name':'sweep','js':"""(() => {
      const samples=[];
      for(const e of document.querySelectorAll('input[type=range]')) {
        document.getElementById('reset').click();
        for(let i=0;i<=20;i++) {
          e.value=Number(e.min)+(Number(e.max)-Number(e.min))*i/20;
          e.dispatchEvent(new Event('input',{bubbles:true}));
          samples.push(JSON.parse(JSON.stringify(window.__lessonDebug)));
        }
      }
      return samples;
    })()"""})
    report=inspect(args.html,args.output,actions)
    samples=report['states'][-1]['result'].get('result',{}).get('value',[])
    errors=[];checked=0;max_residual=0
    cross=lambda a,b,c:(b[0]-a[0])*(c[1]-a[1])-(b[1]-a[1])*(c[0]-a[0])
    area=lambda a,b,c:abs(cross(a,b,c))/2
    for sample in samples:
        d=sample['data']
        try:
            if args.case=='ellipse':
                P,Q,A,B,M,N=(d[k] for k in ('P','Q','A','B','M','N'))
                residuals=[abs(p[0]**2/4+p[1]**2/2-1) for p in (P,Q,A,B)]
                residuals += [abs((P[i]+N[i])/2-M[i]) for i in (0,1)]
                residuals += [abs(N[1]),abs(M[0]),abs(P[0]-Q[0]),abs(P[1]+Q[1]),abs(cross(A,M,P)),abs(cross(B,M,Q))]
                k1=(P[1]-M[1])/(P[0]-M[0]);k2=(Q[1]-M[1])/(Q[0]-M[0]);kab=(B[1]-A[1])/(B[0]-A[0])
                residuals += [abs(k2/k1+3),abs(kab-(1/(4*k1)+1.5*k1))]
                if 'kAB' in d:residuals.append(abs(d['kAB']-kab))
                assert P[0]>0 and P[1]>0
                assert kab>=math.sqrt(6)/2-1e-8
            else:
                A,B,C,G,M,N=(d[k] for k in ('A','B','C','G','M','N'))
                residuals=[abs(p[0]**2-8*p[1]) for p in (A,B,C)]
                residuals += [abs(sum(p[i] for p in (A,B,C))/3-G[i]) for i in (0,1)]
                residuals += [abs(G[0]),abs(M[0]),abs(N[0]),abs(cross(A,B,M)),abs(cross(B,C,N))]
                R=(area(A,M,G)+area(C,N,G))/area(A,B,C)
                t=C[0]/A[0];residuals.append(abs(R-(t*t+4*t+1)/(3*(t+2)*(2*t+1))))
                assert A[0]>0 and C[0]>0 and B[0]<0
                assert 3*math.dist(A,M)<math.dist(B,M)
                assert 1/6<R<13/60
                for key in ('R','ratio'):
                    if isinstance(d.get(key),(int,float)):residuals.append(abs(d[key]-R))
            worst=max(residuals);max_residual=max(max_residual,worst)
            assert worst<1e-6, f'residual={worst}'
            checked+=1
        except (KeyError,AssertionError,TypeError,ZeroDivisionError) as exc:
            errors.append({'params':sample['params'],'error':f'{type(exc).__name__}: {exc}'})
    result={'case':args.case,'sample_count':len(samples),'checked':checked,'max_residual':max_residual,
            'math_errors':errors,'runtime_errors':report['runtime_errors'],
            'passed':bool(samples) and not errors and not report['runtime_errors']}
    (args.output/'independent_check.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(result,ensure_ascii=False,indent=2))


if __name__=='__main__':main()
