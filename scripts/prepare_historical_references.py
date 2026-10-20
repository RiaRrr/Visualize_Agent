"""Render independently authored reference solutions and numerically check their identities.
No candidate HTML, model call, or human score is read. Run after importing historical examples.
"""
import json,math,html,sys
from pathlib import Path
sys.path.insert(0,str(Path.cwd()))
from app.benchmark import BENCH,read,write
root=BENCH/'人工评分';manifest=read(root/'manifest.json')
references={
'HQ0822E89A4F0E':('抛物线、重心与面积比','Γ：x²=8y，y>0；面积比范围 (1/6,13/60)。'),
'HQC5BAAA3E416B':('椭圆对称弦','m∈(−∞,−√(2/3))∪(√(2/3),+∞)；最大面积 √2/2，在 m=±√2 时取得。')}
area=lambda a,b,c:abs((b[0]-a[0])*(c[1]-a[1])-(b[1]-a[1])*(c[0]-a[0]))/2
par=[]
for a in [.3,1.,4.]:
 for r in [2.0001,2.2,3.,5.,20.,100.]:
  c=r*a;b=-a-c;A=(a,a*a/8);B=(b,b*b/8);C=(c,c*c/8);G=(0,(a*a+a*c+c*c)/12);M=(0,a*(a+c)/8);N=(0,c*(a+c)/8)
  measured=(area(A,M,G)+area(C,N,G))/area(A,B,C);formula=(r*r+4*r+1)/(3*(r+2)*(2*r+1))
  assert 1/6<measured<13/60 and 3*math.dist(A,M)<math.dist(B,M)
  par.append(abs(measured-formula))
ell=[]
for m in [-10.,-2.,-math.sqrt(2),-.82,.82,math.sqrt(2),2.,10.]:
 D=(-1/m,-.5);t=math.sqrt((3*m*m-2)/(2*m*m*(m*m+2)));A=(D[0]-t*m,D[1]+t);B=(D[0]+t*m,D[1]-t)
 S=area((0,0),A,B);formula=math.sqrt((3*m*m-2)*(m*m+2)/(8*m**4))
 errors=[abs(S-formula),abs(A[0]**2/2+A[1]**2-1),abs(B[0]**2/2+B[1]**2-1),abs(D[1]-m*D[0]-.5)]
 # Reflection across m*x-y+1/2=0.
 signed=(m*A[0]-A[1]+.5)/(m*m+1);reflected=(A[0]-2*m*signed,A[1]+2*signed)
 errors += [math.dist(reflected,B)];ell.extend(errors)
 if abs(abs(m)-math.sqrt(2))<1e-9:assert abs(S-math.sqrt(2)/2)<1e-12
 assert S<=math.sqrt(2)/2+1e-12
assert max(par+ell)<1e-10
style='<meta charset="utf-8"><style>body{font:20px/1.8 "Noto Sans CJK SC",sans-serif;padding:28px;color:#17343f}h1{font-size:26px}h2{font-size:22px}p{margin:12px 0;overflow-wrap:anywhere}</style>'
for q in manifest['questions']:
 title,answer=references[q['id']];folder=BENCH/q['path']/'reference';text=(folder/'解析.md').read_text()
 parts=[]
 for line in text.splitlines():
  if line.startswith('# '):parts.append('<h1>'+html.escape(line[2:])+'</h1>')
  elif line.startswith('## '):parts.append('<h2>'+html.escape(line[3:])+'</h2>')
  else:parts.append('<p>'+html.escape(line)+'</p>')
 (folder/'explanation.html').write_text('<!doctype html><html>'+style+'<body>'+''.join(parts)+'</body></html>')
 (folder/'answer.html').write_text('<!doctype html><html>'+style+'<body><h1>'+title+'</h1><p>'+answer+'</p><p>本次独立推导与数值核验；非题库原答案，待人工复核。</p></body></html>')
 write(folder/'checks.json',{'status':'agent_checked_pending_human','source':'independent_solution_from_original_image','not_human_ground_truth':True,'not_taken_from_candidate_html':True,'answer':answer,'derivation':text.splitlines(),'numerical_validation':{'samples':18 if q['id'].startswith('HQ0822') else 8,'max_residual':max(par) if q['id'].startswith('HQ0822') else max(ell)},'checks':['端点与退化情形已在推导中说明；数值采样不能替代解析证明']})
 q.update(topic=title,has_reference=True,reference_status='agent_checked_pending_human')
write(root/'manifest.json',manifest)
print(json.dumps({'parabola_max_residual':max(par),'ellipse_max_residual':max(ell)},ensure_ascii=False))
