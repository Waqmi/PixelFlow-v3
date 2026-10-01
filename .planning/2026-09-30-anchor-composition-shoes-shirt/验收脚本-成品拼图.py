import sys,glob,os
from PIL import Image, ImageDraw
folder, out, tw = sys.argv[1], sys.argv[2], int(sys.argv[3])
fs=sorted(glob.glob(folder+'/*.jpg'))
ims=[]
for f in fs:
    im=Image.open(f).convert('RGB'); im.thumbnail((tw, tw*4//3)); d=ImageDraw.Draw(im); d.text((3,3),os.path.basename(f)[:22],fill=(255,0,0)); ims.append(im)
cols=int(sys.argv[4]) if len(sys.argv)>4 else 10
w=max(i.width for i in ims); h=max(i.height for i in ims); rows=(len(ims)+cols-1)//cols
c=Image.new('RGB',(cols*(w+4),rows*(h+4)),'gray')
for i,im in enumerate(ims): c.paste(im,((i%cols)*(w+4),(i//cols)*(h+4)))
c.save(out); print(len(ims), c.size)
