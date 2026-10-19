"""Independent ellipse check using actual SVG coordinates, without editing HTML."""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

from scripts.inspect_1019 import inspect


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('html', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    actions = [{'name': f'step_{i + 1}', 'js': "document.getElementById('nextBtn').click()"}
               for i in range(1, 6)]
    actions += [{'name': 'minimum', 'js': "document.getElementById('minBtn').click()"}]
    actions += [{'name': 'sweep', 'js': """(() => {
      const svg = document.getElementById('mainSvg'), ellipse = svg.querySelector('ellipse');
      const attr=(e,k)=>Number(e.getAttribute(k));
      const cx=attr(ellipse,'cx'), cy=attr(ellipse,'cy'), scale=attr(ellipse,'rx')/2;
      const e=document.getElementById('tSlider'), samples=[];
      const labelsOverlap=()=>{
        const a=document.getElementById('miniCurLabel'), b=document.getElementById('miniMinLabel');
        if(!a || !b)return false;
        if(!a.textContent.trim() || getComputedStyle(a).opacity==='0' || getComputedStyle(b).opacity==='0')return false;
        const x=a.getBoundingClientRect(), y=b.getBoundingClientRect();
        return Math.min(x.right,y.right)-Math.max(x.left,y.left)>1 && Math.min(x.bottom,y.bottom)-Math.max(x.top,y.top)>1;
      };
      const read=()=>({t:Number(e.value),points:Object.fromEntries(['A','B','P','Q','M','N'].map(k=>{
        const dot=document.getElementById('dot'+k);
        return [k,[(attr(dot,'cx')-cx)/scale,(cy-attr(dot,'cy'))/scale]];
      })),kAB:document.getElementById('roKAB').innerText,labelsOverlap:labelsOverlap()});
      const minimum=read();
      for(let i=0;i<=20;i++){
        e.value=Number(e.min)+(Number(e.max)-Number(e.min))*i/20;
        e.dispatchEvent(new Event('input',{bubbles:true}));samples.push(read());
      }
      return {minimum,samples,axisScaleRatio:(attr(ellipse,'ry')/Math.SQRT2)/scale};
    })()"""}]
    report = inspect(args.html, args.output, actions)
    data = report['states'][-1]['result'].get('result', {}).get('value', {})
    errors, residuals = [], []
    cross = lambda a,b,c:(b[0]-a[0])*(c[1]-a[1])-(b[1]-a[1])*(c[0]-a[0])
    samples = data.get('samples', [])
    for sample in samples + ([data['minimum']] if 'minimum' in data else []):
        d = sample['points']; A,B,P,Q,M,N = (d[k] for k in ('A','B','P','Q','M','N'))
        k1=(P[1]-M[1])/(P[0]-M[0]); k2=(Q[1]-M[1])/(Q[0]-M[0])
        kab=(B[1]-A[1])/(B[0]-A[0])
        r=[abs(p[0]**2/4+p[1]**2/2-1) for p in (A,B,P,Q)]
        r += [abs((P[i]+N[i])/2-M[i]) for i in (0,1)]
        r += [abs(N[1]),abs(M[0]),abs(cross(A,M,P)),abs(cross(B,M,Q)),
              abs(P[0]-Q[0]),abs(P[1]+Q[1]),abs(k2/k1+3),abs(kab-(1/(4*k1)+1.5*k1))]
        residuals.extend(r)
        if max(r)>1e-5 or abs(float(sample['kAB'])-kab)>.001 or kab<math.sqrt(6)/2-1e-6:
            errors.append({'sample':sample,'residual':max(r)})
    if data.get('minimum'):
        A,B=(data['minimum']['points'][k] for k in ('A','B'))
        minimum_error=abs((B[1]-A[1])/(B[0]-A[0])-math.sqrt(6)/2)
    else:
        minimum_error=None
    result={'html':str(args.html),'sample_count':len(samples),'max_residual':max(residuals,default=None),
            'minimum_error':minimum_error,'axis_scale_ratio':data.get('axisScaleRatio'),
            'math_errors':errors,'runtime_errors':report['runtime_errors'],
            'label_collisions':[s['t'] for s in samples if s['labelsOverlap']],
            'passed':len(samples)==21 and not errors and not report['runtime_errors']
                     and not any(s['labelsOverlap'] for s in samples)
                     and minimum_error is not None and minimum_error<1e-6
                     and abs(data['axisScaleRatio']-1)<.001}
    (args.output/'independent_check.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps(result,ensure_ascii=False,indent=2))


if __name__=='__main__':
    main()
