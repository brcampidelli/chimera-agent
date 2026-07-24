import re,sys,collections
rows=[]
for ln in open(sys.argv[1],encoding="utf-8",errors="replace"):
    m=re.match(r"\s*\[\s*\d+/40\]\s+(\S+)\s+(\S+)\s+(PASS|FAIL)\s+att=(\S+)\s+inj=(\d+)/(\d+)",ln)
    if m: rows.append(list(m.groups()))
# assign seeds: each arm block of 40 in order control,i1,i2 per seed
seq=collections.defaultdict(int)
for r in rows:
    seq[r[0]]+=1
    r.append((seq[r[0]]-1)//40 + 1)   # seed number
print(f"progresso: {len(rows)}/360\n")
for arm in ("control","i1","i2"):
    r=[x for x in rows if x[0]==arm]
    if not r: continue
    p=sum(1 for x in r if x[2]=="PASS"); att=[int(x[3]) for x in r if x[3].isdigit()]
    ret=[a for a in att if a>=2]
    rec=sum(1 for x in r if x[2]=="PASS" and x[3].isdigit() and int(x[3])>=2)
    print(f"  {arm:<8} n={len(r):>3}  pass {p/len(r):6.1%}  A {(len(ret)/len(att) if att else 0):6.1%}  "
          f"R {(rec/len(ret) if ret else 0):6.1%}   inj: diff={sum(int(x[4]) for x in r)} stall={sum(int(x[5]) for x in r)}")
idx={(x[0],x[6],x[1]):x for x in rows}
for arm in ("i1","i2"):
    keys=sorted({(k[1],k[2]) for k in idx if k[0]=="control"} & {(k[1],k[2]) for k in idx if k[0]==arm})
    if not keys: continue
    b=sum(1 for k in keys if idx[("control",)+k][2]=="PASS" and idx[(arm,)+k][2]=="FAIL")
    c=sum(1 for k in keys if idx[("control",)+k][2]=="FAIL" and idx[(arm,)+k][2]=="PASS")
    print(f"\n  {arm.upper()} vs control (pareado, n={len(keys)}): {arm} +{c} / control +{b}  ->  delta {(c-b)/len(keys):+.1%}")
    both=[k for k in keys if idx[("control",)+k][3].isdigit() and int(idx[("control",)+k][3])>=2
                          and idx[(arm,)+k][3].isdigit() and int(idx[(arm,)+k][3])>=2]
    if both:
        cb=sum(1 for k in both if idx[("control",)+k][2]=="PASS")
        ab=sum(1 for k in both if idx[(arm,)+k][2]=="PASS")
        print(f"    lente de retry (n={len(both)}): control {cb/len(both):.1%} vs {arm} {ab/len(both):.1%}")
