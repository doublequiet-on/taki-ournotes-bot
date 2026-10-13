# L3
# Input: Original note kind, lane width and source skin atlas.
# Output: Cached native note sprites, reconstructed with mesh alpha and end caps.
# Pos: Rendering / Song; original game note artwork used by approved samples.
# Effects: Reads caller-supplied atlas; bounded raster caches, no network.
import json
from functools import lru_cache
from PIL import Image,ImageDraw,ImageOps,ImageChops
from io import BytesIO
from .haneoka_theme import game_asset

@lru_cache(maxsize=1)
def _skin(config, atlas):
    with Image.open(BytesIO(atlas)) as source:
        image = source.convert('RGBA')
    return json.loads(config), image


def skin():
    config, atlas = game_asset('skin001.json'), game_asset('skin001.png')
    if not config or not atlas:
        raise ValueError('Original note skin unavailable')
    return _skin(config, atlas)

@lru_cache(maxsize=80)
def sprite(name):
    SKIN,ATLAS=skin()
    sd=SKIN['sprites'][name]['data'];rd=next(v for k,v in SKIN['atlas']['data']['m_RenderDataMap'] if k==sd['m_RenderDataKey'])
    rect=rd['textureRect'];x,y,w,h=[rect[k] for k in ('x','y','width','height')]
    tile=ATLAS.crop((round(x),round(ATLAS.height-y-h),round(x+w),round(ATLAS.height-y)))
    fw,fh=[round(sd['m_Rect'][k]) for k in ('width','height')];full=Image.new('RGBA',(fw,fh))
    ox,oy=[rd['textureRectOffset'][k] for k in ('x','y')];full.paste(tile,(round(ox),round(fh-oy-h)))
    mesh=sd.get('originalRenderData',{}).get('mesh')
    if mesh:
        mask=Image.new('L',(fw,fh));md=ImageDraw.Draw(mask);pu=sd['m_PixelsToUnits'];pv=sd['m_Pivot']
        pts=[(p[0]*pu+fw*pv['x'],fh*(1-pv['y'])-p[1]*pu) for p in mesh['positions']]
        for i in range(0,len(mesh['indices']),3):md.polygon([pts[j] for j in mesh['indices'][i:i+3]],fill=255)
        full.putalpha(ImageChops.darker(full.getchannel('A'),mask))
    return full


@lru_cache(maxsize=384)
def note(kind,width,size):
    SKIN,_=skin()
    spec=SKIN['notes'][kind];height=16 if kind=='slide-node' else 20
    part=spec['parts'][0];left=sprite(part['leftSprite']);right=sprite(part['rightSprite']);body=sprite(spec['mainSprite'])
    lw=max(2,round(left.width/left.height*height));rw=max(2,round(right.width/right.height*height));width=max(lw+rw+2,width)
    result=Image.new('RGBA',(width,52));y=32
    for image,x,w in ((left,0,lw),(body,lw,width-lw-rw),(right,width-rw,rw)):
        image=image.resize((w,height),Image.Resampling.LANCZOS);result.paste(image,(x,y),image)
    if spec['centerMarkSprite']:
        image=ImageOps.contain(sprite(spec['centerMarkSprite']),(16,16),Image.Resampling.LANCZOS)
        result.paste(image,((width-image.width)//2,y+(height-image.height)//2),image)
    if spec['arrows']:
        name=next(ar['sprite'] for ar in spec['arrows'] if size<=ar['maxWidth'])
        image=sprite(name)
        if kind=='flick-left':image=ImageOps.mirror(image)
        image=ImageOps.contain(image,(max(24,round(width*.75)),27),Image.Resampling.LANCZOS)
        result.paste(image,((width-image.width)//2,31-image.height),image)
    return result


def draw_note(canvas,x,y,width,kind,size):
    image=note(kind,max(1,round(width)),size)
    canvas.image.paste(image,(round(x-image.width/2),round(y-42)),image)
