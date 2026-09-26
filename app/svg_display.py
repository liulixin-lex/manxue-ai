"""Presentation-only SVG edges; stored originals and review evidence stay immutable."""
import math
import re
import xml.etree.ElementTree as ET


def _length(value, extent):
    match=re.fullmatch(r'\s*([+-]?(?:\d+(?:\.\d*)?|\.\d+))(px|%)?\s*',str(value))
    if not match:return None
    number=float(match[1])
    return number*extent/100 if match[2]=='%' else number


def _style(element, **values):
    declarations=element.get('style','').rstrip(';')
    element.set('style',declarations+';'+';'.join(f'{k.replace("_","-")}:{v}!important' for k,v in values.items()))


def display_svg(svg):
    """Square canvas clips and suppress canvas-sized decorative outlines only."""
    try:
        root=ET.fromstring(svg)
        if root.tag.split('}')[-1]!='svg':return svg
        viewbox=root.get('viewBox','').replace(',',' ').split()
        if len(viewbox)==4:
            x,y,width,height=map(float,viewbox)
        else:
            x=y=0
            width=_length(root.get('width',''),0)
            height=_length(root.get('height',''),0)
        if any(v is None or not math.isfinite(v) for v in (x,y,width,height)) or min(width,height)<=0:return svg
    except (ET.ParseError,ValueError,OverflowError):
        return svg
    _style(root,border='0',border_radius='0',padding='0',margin='0',box_shadow='none')
    parents={child:parent for parent in root.iter() for child in parent}
    for rect in root.iter():
        if rect.tag.split('}')[-1]!='rect':continue
        ancestor=rect
        local=False
        while ancestor is not root:
            if ancestor.tag.split('}')[-1] in ('pattern','symbol','marker','svg') or ancestor.get('transform'):
                local=True;break
            ancestor=parents.get(ancestor,root)
        if local:continue
        left=_length(rect.get('x','0'),width);top=_length(rect.get('y','0'),height)
        w=_length(rect.get('width',''),width);h=_length(rect.get('height',''),height)
        if any(v is None or not math.isfinite(v) for v in (left,top,w,h)):continue
        gaps=(left-x,top-y,x+width-left-w,y+height-top-h)
        full=all(abs(g)<=.1 for g in gaps)
        style=dict(part.split(':',1) for part in rect.get('style','').split(';') if ':' in part)
        fill=style.get('fill',rect.get('fill','')).strip().lower()
        outline=fill in ('none','transparent')
        frame=outline and all(-.1<=g<=limit for g,limit in zip(gaps,(width*.05,height*.05,width*.05,height*.05)))
        if full:
            rect.set('rx','0');rect.set('ry','0')
            _style(rect,rx='0',ry='0',stroke='none')
        elif frame:
            _style(rect,stroke='none')
    ET.register_namespace('','http://www.w3.org/2000/svg')
    return ET.tostring(root,encoding='unicode')
