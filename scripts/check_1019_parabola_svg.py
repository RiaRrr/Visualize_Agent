"""Case-specific check of displayed SVG geometry; no generation hints or API calls."""
import argparse
import json
import math
from pathlib import Path

from scripts.inspect_1019 import inspect


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('html',type=Path)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    actions=[{'name':'area','js':"document.querySelector('[data-step=\"4\"]').click()"},
             {'name':'sweep','js':"""(() => {
      const samples=[], e=document.getElementById('rSlider');
      for(let i=0;i<=20;i++){
        e.value=Number(e.min)+(Number(e.max)-Number(e.min))*i/20;
        e.dispatchEvent(new Event('input',{bubbles:true}));
        // This saved page uses the inspected mapping X=380+30x, Y=480-30y.
        const dots=[...document.querySelectorAll('#mainSvg circle.point')].slice(2,8);
        samples.push({r:Number(e.value),points:Object.fromEntries(['A','B','C','G','M','N'].map((k,j)=>
          [k,[(Number(dots[j].getAttribute('cx'))-380)/30,(480-Number(dots[j].getAttribute('cy')))/30]]))});
      }
      return samples;
    })()"""}]
    report=inspect(args.html,args.output,actions)
    samples=report['states'][-1]['result'].get('result',{}).get('value',[])
    cross=lambda a,b,c:(b[0]-a[0])*(c[1]-a[1])-(b[1]-a[1])*(c[0]-a[0])
    area=lambda a,b,c:abs(cross(a,b,c))/2
    errors=[];max_residual=0
    for sample in samples:
        A,B,C,G,M,N=(sample['points'][k] for k in ('A','B','C','G','M','N'))
        residuals=[abs(p[0]**2-8*p[1]) for p in (A,B,C)]
        residuals += [abs(sum(p[i] for p in (A,B,C))/3-G[i]) for i in (0,1)]
        residuals += [abs(G[0]),abs(M[0]),abs(N[0]),abs(cross(A,B,M)),abs(cross(B,C,N))]
        R=(area(A,M,G)+area(C,N,G))/area(A,B,C); t=C[0]/A[0]
        residuals += [abs(t-sample['r']),abs(R-(t*t+4*t+1)/(3*(t+2)*(2*t+1)))]
        max_residual=max(max_residual,max(residuals))
        if max(residuals)>1e-6 or not (3*math.dist(A,M)<math.dist(B,M) and 1/6<R<13/60):
            errors.append(sample)
    result={'html':str(args.html),'samples':len(samples),'max_residual':max_residual,
            'math_errors':errors,'runtime_errors':report['runtime_errors'],
            'passed':len(samples)==21 and not errors and not report['runtime_errors']}
    (args.output/'independent_check.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps(result,ensure_ascii=False,indent=2))


if __name__=='__main__':
    main()
