import sys,glob,json; sys.path.insert(0,'.')
from pathlib import Path
from PIL import Image, ImageDraw
from pixelflow_core.model_assistant import LocalModelAssistant, LocalSegmentationAssistant, BODY_KEYPOINT_CONFIDENCE
pose=LocalModelAssistant('../models/pose/yolo11n-pose.onnx'); seg=LocalSegmentationAssistant('../models/segmentation/yolo11n-seg.onnx')
SIZES={'1440x1440':((1440,1440),(255,255,255)),'1440x1920':((1440,1920),(239,239,239)),'1125x1500':((1125,1500),(239,239,239))}
cat=sys.argv[1]; folder=sys.argv[2]; out=sys.argv[3]
tiles=[]; rows=[]
for f in sorted(glob.glob(folder+'/*.jpg')):
    src=Path(f); s=seg.segment(src)
    r=pose.compose_for_sizes(src,SIZES,cat,segmentation=s)
    name=src.name.replace('2026-','')
    if 'by_size' not in r: rows.append(f"{name}: {r['model_view']} 未进入构图"); continue
    im=Image.open(f).convert('RGB'); W,H=im.size
    ds=pose.detect(src)
    def ys(idx): return [d.keypoints[i][1]/H for d in ds for i in idx if d.keypoints[i][2]>=BODY_KEYPOINT_CONFIDENCE]
    knee=ys((13,14)); hip=ys((11,12))
    line=[f"{name} n={r['person_count']} view={r['model_view']}"]
    crops=[]
    for size,p in r['by_size'].items():
        if 'crop_box' not in p: line.append(f"{size}: 旧逻辑({p.get('composition_mode')})"); continue
        l,t,rr,b=p['crop_box']
        info=f"{size}: 窗[{t:.2f}-{b:.2f}]"
        if cat=='shoes' and knee: info+=f" 膝{min(knee):.2f}{'✓' if t>min(knee) else '✗露膝'}"
        if cat=='shirt' and hip: info+=f" 胯{max(hip):.2f}"
        if p.get('review_hint'): info+=f" ⚠{p['review_hint']}"
        line.append(info)
        tw,th=SIZES[size][0]
        c=im.crop((round(l*W),round(t*H),round(rr*W),round(b*H))).resize((tw//8,th//8))
        crops.append(c)
    rows.append(' | '.join(line))
    if crops:
        tile=Image.new('RGB',(sum(c.width for c in crops)+10*len(crops),245),'white'); x=0
        for c in crops: tile.paste(c,(x,0)); x+=c.width+10
        ImageDraw.Draw(tile).text((2,2),name,fill=(255,0,0)); tiles.append(tile)
cols=4; tw_=max(t.width for t in tiles); n=(len(tiles)+cols-1)//cols
sheet=Image.new('RGB',(cols*(tw_+10),n*250),'gray')
for i,t in enumerate(tiles): sheet.paste(t,((i%cols)*(tw_+10),(i//cols)*250))
sheet.save(out); print('\n'.join(rows))
