import bpy, sys, json, re, math
import numpy as np
from mathutils import Vector
src, outdir = sys.argv[-2], sys.argv[-1]
bpy.ops.wm.open_mainfile(filepath=src, use_scripts=False)
bpy.context.scene.frame_set(1)
mods=set()
def anc(o):
    while o:
        yield o; o=o.parent
def matinfo(m):
    if m is None: return (0.5,0.5,0.5),False
    nt=m.node_tree
    bs=nt.nodes.get('Principled BSDF') if nt else None
    if m.name=='PlanetMat': return (0.16,0.42,0.62),True
    if m.name=='MoonMat': return (0.62,0.6,0.58),True
    if not bs: return (0.5,0.5,0.5),False
    es=bs.inputs['Emission Strength'].default_value
    if m.name=='Screen': es=2.5
    if es>0:
        c=np.array(bs.inputs['Emission Color'].default_value[:3]); k=min(1.0,0.55+es/8.0)
        return tuple(np.clip(c*k+ (0.15 if es>4 else 0),0,1)),True
    return tuple(bs.inputs['Base Color'].default_value[:3]),False
def lin2srgb(c): return [ (12.92*x if x<0.0031308 else 1.055*x**(1/2.4)-0.055) for x in c]
lit=[];emi=[];col=[]
skipname=re.compile(r'^(Haze)')
stats={}
for o in bpy.data.objects:
    if o.type!='MESH' or skipname.match(o.name): continue
    for m in o.modifiers: mods.add(m.type)
    names=[a.name for a in anc(o)]
    onrack=any(n.startswith('Rack') for n in names[1:])
    if onrack and (o.name.startswith('Leaf') or o.name.startswith('Tray')): continue
    me=o.data; me.calc_loop_triangles()
    if not me.loop_triangles: continue
    M=np.array(o.matrix_world); R=np.array(o.matrix_world.to_3x3().inverted().transposed())
    v=np.array([x.co[:] for x in me.vertices]); vw=v@M[:3,:3].T+M[:3,3]
    tris=np.array([t.vertices[:] for t in me.loop_triangles])
    tn=np.array([ (t.normal[:] if not t.use_smooth else (0,0,0)) for t in me.loop_triangles])
    sm=np.array([t.use_smooth for t in me.loop_triangles])
    vn=np.array([x.normal[:] for x in me.vertices])
    mi=np.array([t.material_index for t in me.loop_triangles])
    P=vw[tris]                       # T,3,3
    N=np.where(sm[:,None,None], vn[tris], tn[:,None,:].repeat(3,1))
    N=N@R.T; N/= (np.linalg.norm(N,axis=2,keepdims=True)+1e-9)
    mats=[matinfo(s.material) for s in o.material_slots] or [((0.5,0.5,0.5),False)]
    C=np.zeros((len(tris),3)); E=np.zeros(len(tris),bool)
    for i,(c,e) in enumerate(mats):
        sel=mi==i; C[sel]=lin2srgb(c); E[sel]=e
    if o.name=='Planet':
        cen=P.mean(1); d=cen-cen.mean(0); d/=np.linalg.norm(d,axis=1,keepdims=True)
        n=np.sin(d[:,0]*7+np.sin(d[:,2]*5)*2)*np.cos(d[:,1]*6+d[:,0]*3)+0.5*np.sin(d[:,2]*17+d[:,1]*11)
        land=n>0.55; C[land]=(0.30,0.52,0.30); C[n>1.0]=(0.55,0.5,0.38); C[np.abs(d[:,2])>0.9]=(0.9,0.93,0.95)
        C*= (0.75+0.25*np.clip(d@np.array([0.5,-0.6,0.6]),0,1))[:,None]
    C=np.repeat(C[:,None,:],3,1)
    rec=np.concatenate([P,N,C],axis=2).reshape(-1,9).astype(np.float32)
    k=re.sub(r'[\d\.\-_]+$','',o.name); stats[k]=stats.get(k,0)+len(tris)
    for e,dst in ((True,emi),(False,lit)):
        sel=np.repeat(E==e,3)
        if sel.any(): dst.append(rec[sel])
    robot=any(n.startswith('Robot_') for n in names)
    mn=vw.min(0); mx=vw.max(0); dims=mx-mn
    far=np.abs(vw).max()>100
    small=np.linalg.norm(dims)<0.45 or re.match(r'^(Leaf|Canopy|Spoke|Tire|Hub|Bristle|Cable|Beacon|Antenna|Mast|TrussWeb|WeldTip|.*Light|.*Glow|EyeBar|Stripe)',o.name)
    if not far and not small and not robot and mn[2]<3.2:
        col.append(P.reshape(-1,3).astype(np.float32))
print('modifiers',mods)
lit=np.concatenate(lit); emi=np.concatenate(emi); col=np.concatenate(col)
print('lit tris',len(lit)//3,'emi tris',len(emi)//3,'col tris',len(col)//3)
print(sorted(stats.items(),key=lambda x:-x[1])[:25])
def bb(o):
    w=[o.matrix_world@Vector(c) for c in o.bound_box]
    return [round(min(b[i] for b in w),3) for i in range(3)],[round(max(b[i] for b in w),3) for i in range(3)]
mk={'racks':[], 'winscreens':[], 'corescreens':[], 'robots':[], 'cams':{}}
shelves={}
for o in bpy.data.objects:
    if o.name.startswith('Shelf') and o.parent: shelves.setdefault(o.parent.name,[]).append(bb(o))
for o in sorted(bpy.data.objects,key=lambda o:o.name):
    if o.type=='EMPTY' and re.match(r'^Rack(\.\d+)?$',o.name):
        mk['racks'].append({'name':o.name,'pos':[round(x,3) for x in o.matrix_world.translation],'shelves':sorted(shelves.get(o.name,[]),key=lambda s:s[0][2])})
    if o.name.startswith('WinScreen'): mk['winscreens'].append(bb(o))
    if o.name.startswith('CoreScreen'): mk['corescreens'].append({'bb':bb(o),'rotz':round(o.matrix_world.to_euler().z,3)})
    if o.type=='EMPTY' and o.name.startswith('Robot_'): mk['robots'].append({'name':o.name,'pos':[round(x,2) for x in o.matrix_world.translation]})
    if o.type=='CAMERA': mk['cams'][o.name]=[round(x,2) for x in o.matrix_world.translation]
np.savez_compressed(outdir+'/ship_geom.npz',lit=lit,emi=emi,col=col)
json.dump(mk,open(outdir+'/markers.json','w'),indent=1)
print(json.dumps(mk)[:1500])
