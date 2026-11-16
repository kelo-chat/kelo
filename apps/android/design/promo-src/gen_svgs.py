import re, json, sys, os
C=os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'character')
out={}
for f in sorted(os.listdir(C)):
    if not f.endswith('.svg'): continue
    st=f[5:-4]; s=open(os.path.join(C,f)).read()
    names=set(re.findall(r'@keyframes\s+([\w-]+)',s))|set(re.findall(r'\.([A-Za-z][\w-]*)\s*\{',s))
    for n in sorted(names,key=len,reverse=True):
        s=re.sub(r'(?<![\w-])'+re.escape(n)+r'(?![\w-])', st+'__'+n, s)
    s=s.replace('width="100%" height="100%"','width="100%" height="100%" class="mz"')
    out[st]=s
open(sys.argv[1],'w').write('window.MASCOT='+json.dumps(out)+';')
print(list(out))
