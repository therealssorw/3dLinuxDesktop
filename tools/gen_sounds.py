import numpy as np, wave, sys
out=sys.argv[1]; SR=22050
rng=np.random.default_rng(7)
def save(name,x):
    x=np.clip(x,-1,1); w=wave.open(f"{out}/{name}.wav","wb"); w.setnchannels(1); w.setsampwidth(2); w.setframerate(SR)
    w.writeframes((x*32000).astype('<i2').tobytes()); w.close()
def t(d): return np.arange(int(SR*d))/SR
def env(x,a=0.005,r=0.05):
    n=len(x); e=np.ones(n); na=int(a*SR); nr=int(r*SR)
    e[:na]=np.linspace(0,1,na); e[-nr:]=np.linspace(1,0,nr); return x*e
def lp(x,k):
    y=np.empty_like(x); a=0.0
    for i,v in enumerate(x): a+=k*(v-a); y[i]=a
    return y
# ambient hum: loopable 6s (integer cycles)
d=6.0; tt=t(d)
hum=0.22*np.sin(2*np.pi*55*tt)+0.12*np.sin(2*np.pi*110*tt+0.4)+0.06*np.sin(2*np.pi*165.5*tt)*(0.6+0.4*np.sin(2*np.pi*0.5*tt))
n=lp(rng.normal(0,1,len(tt)),0.02)*0.5; fade=int(0.5*SR); n[:fade]=n[:fade]*np.linspace(0,1,fade)+n[-fade:]*np.linspace(1,0,fade); n=n[:-fade]
hum=hum[:len(n)]+n*(0.7+0.3*np.sin(2*np.pi*(1/5.5)*tt[:len(n)]))
save('ambient',hum*0.8)
# footsteps (metal deck): 2 variants
for i,f in enumerate((95,120)):
    tt=t(0.16); x=np.sin(2*np.pi*f*tt*(1-1.5*tt))*np.exp(-tt*38)+0.35*lp(rng.normal(0,1,len(tt)),0.3)*np.exp(-tt*60)
    save(f'step{i}',env(x*0.7,0.002,0.03))
tt=t(0.07); save('focus',env(0.35*np.sin(2*np.pi*1320*tt)*np.exp(-tt*45),0.002,0.02))
tt=t(0.28); save('open',env(0.4*(np.sin(2*np.pi*660*tt)*(tt<0.09)+np.sin(2*np.pi*990*tt)*(tt>=0.09))*np.exp(-tt*6),0.004,0.08))
tt=t(0.5); f=200+900*tt/0.5; save('whoosh',env(0.5*lp(rng.normal(0,1,len(tt)),0.15)*np.sin(np.pi*tt/0.5)**2+0.12*np.sin(2*np.pi*np.cumsum(f)/SR)*np.sin(np.pi*tt/0.5),0.01,0.1))
tt=t(0.22); save('thud',env((np.sin(2*np.pi*70*tt)*np.exp(-tt*30)+0.5*lp(rng.normal(0,1,len(tt)),0.2)*np.exp(-tt*50))*0.8,0.002,0.05))
tt=t(0.2); save('deny',env(0.3*np.sign(np.sin(2*np.pi*140*tt))*np.exp(-tt*10),0.004,0.05))
tt=t(0.12); save('grab',env(0.35*np.sin(2*np.pi*(500+2500*tt)*tt)*np.exp(-tt*20),0.003,0.03))
tt=t(0.6); save('dock',env(0.35*np.sin(2*np.pi*np.cumsum(880-700*tt/0.6)/SR)*np.exp(-tt*4),0.01,0.2))
