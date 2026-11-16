"""Build a silent data-film draft from verified local model results.

Run in a Nix shell:
uv run --extra pdf-llm --with imageio-ffmpeg --with fonttools --with brotli python tools/build_video_draft.py
This script does not record or imitate the website. It does not create a voice.
"""
import argparse
import json
import math
import subprocess
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont
from fontTools.ttLib import TTFont
import imageio_ffmpeg
import pypdfium2

from dolos.model_demo import analyze_demo, demo_paths

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'output' / 'video'
W, H, FPS = 1920, 1080, 24
PAPER, INK, MUTED = '#f4f3ef', '#292d35', '#626772'
CANVAS, BLUE, RED, LIGHT = '#18232c', '#3658db', '#f07860', '#d8e4ec'
SCENES = [
    (0, 12, '01-open', 'A payment table shows one transfer at a time. It does not show the full path of the money. Dolos puts these connections in a graph.'),
    (12, 29, '02-pipeline', 'Dolos uses one model with two parts. GraphSAGE reads earlier transfers and makes number lists. These lists are called embeddings. XGBoost uses the embeddings and transaction fields to make the final decision.'),
    (29, 46, '03-context', 'The arrows show the direction of each transfer. The model uses history before the target hour. We can inspect the actual earlier transfers that GraphSAGE used. Later transfers do not enter this context.'),
    (46, 68, '04-credit', 'Each result shows credit for the graph features and the direct transaction fields. The credits and the base value add to the final score. XGBoost also shows its tree decisions. These values explain the model. They do not prove a crime.'),
    (68, 82, '05-comparison', 'A useful test must include ordinary payments. In this generated comparison, none of the eight payments passes the model threshold. A transfer without a flag is not a guarantee of safety.'),
    (82, 99, '06-statements', 'The test pack has four synthetic statements and twelve hundred transfers. Each statement includes a declared data export. A local test processed all four documents and scored four hundred thirty-two eligible transfers.'),
    (99, 114, '07-close', 'The notebook compares one baseline with the final hybrid. The website uses synthetic teaching data. Dolos is a prototype for human review. It is not financial advice or proof of money laundering.'),
]


def font(size, weight=400, face='inter'):
    path = OUT / '_fonts' / f'{face}.ttf'
    if not path.exists():
        source = ROOT / 'frontend/node_modules/@fontsource-variable' / face / 'files' / f'{face}-latin-wght-normal.woff2'
        path.parent.mkdir(parents=True, exist_ok=True)
        converted = TTFont(source)
        converted.flavor = None
        converted.save(path)
    result = ImageFont.truetype(str(path), size)
    try:
        result.set_variation_by_axes([weight])
    except (OSError, ValueError):
        pass
    return result


def text(draw, xy, value, size=28, fill=INK, weight=400, face='inter'):
    draw.text(xy, value, font=font(size, weight, face), fill=fill)


def wrap(draw, xy, value, width, size=28, fill=INK, weight=400, leading=1.5):
    words, line, y = value.split(), '', xy[1]
    for word in words:
        candidate = (line + ' ' + word).strip()
        if draw.textlength(candidate, font=font(size, weight)) > width and line:
            text(draw, (xy[0], y), line, size, fill, weight)
            line, y = word, y + size * leading
        else:
            line = candidate
    if line:
        text(draw, (xy[0], y), line, size, fill, weight)
    return y + size * leading


def frame(title, subtitle=''):
    image = Image.new('RGB', (W, H), PAPER)
    draw = ImageDraw.Draw(image)
    text(draw, (76, 47), 'dolos.', 46, INK, 800, 'manrope')
    text(draw, (1270, 64), 'Data animation | Synthetic demo', 22, MUTED)
    draw.line((76, 125, 1844, 125), fill='#d7d9d7', width=2)
    text(draw, (76, 167), title, 64, INK, 800, 'manrope')
    if subtitle:
        wrap(draw, (79, 255), subtitle, 1755, 27, MUTED)
    draw.line((76, 984, 1844, 984), fill='#d7d9d7', width=2)
    text(draw, (77, 1008), 'Verified local run. This draft is not a website screen recording.', 22, MUTED)
    text(draw, (1540, 1008), 'dolos-web.vercel.app', 22, BLUE)
    return image, draw


def arrow(draw, start, end, color, width=4):
    draw.line((start, end), fill=color, width=width)
    angle = math.atan2(end[1] - start[1], end[0] - start[0])
    back = (end[0] - 17 * math.cos(angle), end[1] - 17 * math.sin(angle))
    normal = (-8 * math.sin(angle), 8 * math.cos(angle))
    draw.polygon([end, (back[0]+normal[0], back[1]+normal[1]), (back[0]-normal[0], back[1]-normal[1])], fill=color)


def account(identifier):
    try:
        return json.loads(identifier)[1]
    except (ValueError, TypeError):
        return str(identifier)


def graph(draw, edges, box, hot=None, heading='Transfer graph'):
    x0, y0, x1, y1 = box
    draw.rounded_rectangle(box, radius=16, fill=CANVAS)
    text(draw, (x0+34, y0+27), heading, 27, LIGHT, 650)
    nodes = sorted({account(edge[key]) for edge in edges for key in ('source', 'target')})
    positions = {}
    main = ['0000','0001','0003','0005','0008']
    lane = [(0.13,0.32),(0.13,0.70),(0.37,0.51),(0.61,0.51),(0.85,0.51)]
    if len(nodes) <= 8:
        for node, (x, y) in zip(main,lane):
            if node in nodes:
                positions[node]=(x0+(x1-x0)*x,y0+(y1-y0)*y)
    if len(nodes) > 8:
        for node, (x, y) in zip(main[:4], [(0.08,.3),(.08,.7),(.25,.5),(.43,.5)]):
            if node in nodes:
                positions[node]=(x0+(x1-x0)*x,y0+(y1-y0)*y)
        for index,node in enumerate([item for item in nodes if item not in positions]):
            positions[node]=(x0+(x1-x0)*(.59+(index%4)*.12),y0+(y1-y0)*(.25+(index//4)*.27))
    rest = [node for node in nodes if node not in positions]
    if len(rest) > 9:
        positions = {}
        rest = nodes
    for index, node in enumerate(rest):
        angle = -math.pi/2 + 2*math.pi*index/max(len(rest),1)
        positions[node]=(x0+(x1-x0)*(.52+.33*math.cos(angle)),y0+(y1-y0)*(.54+.33*math.sin(angle)))
    pairs={}
    for edge in edges:
        key=(account(edge['source']),account(edge['target']))
        pairs.setdefault(key,[]).append(edge)
    for (source,target), group in pairs.items():
        a,b=positions[source],positions[target]
        angle=math.atan2(b[1]-a[1],b[0]-a[0])
        start=(a[0]+47*math.cos(angle),a[1]+47*math.sin(angle))
        end=(b[0]-48*math.cos(angle),b[1]-48*math.sin(angle))
        selected=hot and any(edge['id']==hot for edge in group)
        arrow(draw,start,end,RED if selected else '#718a99',6 if selected else 3)
        if len(group)>1:
            middle=((a[0]+b[0])/2,(a[1]+b[1])/2)
            draw.rounded_rectangle((middle[0]-24,middle[1]-15,middle[0]+25,middle[1]+20),radius=5,fill=CANVAS)
            text(draw,(middle[0]-17,middle[1]-12),f'x{len(group)}',19,LIGHT)
    for node, (x,y) in positions.items():
        draw.ellipse((x-43,y-43,x+43,y+43), fill='#263b49', outline='#96aebe', width=2)
        text(draw,(x-32,y-14),node,22,LIGHT,650)
    text(draw,(x0+30,y1-70),'Red: path of the selected transfer. Other paths show transfers or history.',19,'#b4c6d1')
    text(draw,(x0+30,y1-43),'Account IDs | Arrows show flow | Repeated paths are grouped',19,'#b4c6d1')


def make_scenes(data, mixed, ordinary, validation):
    example=next(item for item in data['examples'] if item['id']=='mixed-network')
    by_id={edge['id']:edge for edge in example['graph']['edges']}
    scored=mixed['scores'][0]
    details=scored['explanation']
    selected=by_id[scored['transaction_id']]
    frames=[]
    image,draw=frame('Follow the money. Inspect the reason.', 'One graph connects transaction records with a model decision.')
    graph(draw,example['graph']['edges'],(76,340,1270,940),hot=scored['transaction_id'])
    text(draw,(1330,395),'One hybrid model',36,INK,750,'manrope')
    wrap(draw,(1330,462),'GraphSAGE reads the network. XGBoost makes the decision.',455,31)
    text(draw,(1330,672),'12 generated transfers',28,BLUE,600)
    text(draw,(1330,724),'4 model flags',28,RED,600)
    frames.append(image)

    image,draw=frame('Two parts. One final decision.', 'The graph model and tree classifier work in one pipeline.')
    labels=[('GraphSAGE','Earlier transfers. 128 embedding values.'),('XGBoost','Embedding values + 8 transaction features'),('Review result','One margin and one decision')]
    for index,(name,body) in enumerate(labels):
        x=90+index*625
        draw.rounded_rectangle((x,410,x+535,775),radius=16,fill=CANVAS if index<2 else '#e8edff')
        text(draw,(x+32,450),name,40,LIGHT if index<2 else BLUE,750,'manrope')
        wrap(draw,(x+32,535),body,465,30,LIGHT if index<2 else INK)
        if index<2:
            arrow(draw,(x+550,590),(x+605,590),BLUE,5)
    text(draw,(94,855),'An embedding is a list of numbers that represents an account and its earlier connections.',27,MUTED)
    frames.append(image)

    image,draw=frame('Use earlier transfers. Exclude later transfers.', 'This view uses the actual historical context of the selected result.')
    context=[{'id':row['id'],'source':row['sender_id'],'target':row['receiver_id']} for row in details['context_transactions']]
    context.append(selected)
    graph(draw,context,(76,340,1380,940),hot=selected['id'],heading='Actual sampled context')
    text(draw,(1430,395),f"{account(selected['source'])} to {account(selected['target'])}",35,INK,750)
    wrap(draw,(1430,485),'GraphSAGE uses two layers of incoming and outgoing history.',365,28)
    wrap(draw,(1430,680),'Context shows model input. It does not prove a funding link.',365,26,MUTED)
    frames.append(image)

    image,draw=frame('Credit each part of the finding.', 'These are measured contributions to one XGBoost decision.')
    credits=scored['component_contributions']
    rows=[('Base margin',scored['base_margin']),*( (entry['name'],entry['contribution']) for entry in credits),('Final decision margin',scored['score'])]
    maximum=max(abs(value) for _,value in rows)
    for index,(name,value) in enumerate(rows):
        y=370+index*127
        text(draw,(92,y),name,31,INK,650)
        zero, extent = 800, abs(value)/maximum*650
        draw.line((zero,y-5,zero,y+62),fill='#b9c2c8',width=2)
        draw.rounded_rectangle((zero-extent if value<0 else zero,y+5,zero if value<0 else zero+extent,y+51),radius=5,fill=RED if value<0 else BLUE)
        text(draw,(1570,y-1),f'{value:+.3f}',44,INK,700,'manrope')
    wrap(draw,(92,900),'Base + graph features + transaction fields = final margin, before rounding. These are not confidence percentages.',1720,25,MUTED)
    frames.append(image)

    image,draw=frame('Test ordinary payments too.', 'The comparison uses eight generated payments, not a promise of safety.')
    for index,item in enumerate(ordinary['scores']):
        col,row=index%4,index//4
        x,y=88+col*465,365+row*245
        draw.rounded_rectangle((x,y,x+425,y+205),radius=12,fill='#e7eae8')
        text(draw,(x+26,y+27),f'Transfer {index+1}',25,MUTED)
        text(draw,(x+26,y+77),f"{item['score']:+.3f}",48,INK,750,'manrope')
        text(draw,(x+26,y+151),'Below the decision boundary',21,MUTED)
    text(draw,(90,901),'0 model flags in this comparison. An unflagged transfer still needs appropriate review.',27,MUTED)
    frames.append(image)

    image,draw=frame('From statement batches to a graph.', 'A verified local test processed these documents. This is not a live upload recording.')
    manifest=json.loads((ROOT/'output/pdf/manifest.json').read_text())
    for index,item in enumerate(manifest['files']):
        document=pypdfium2.PdfDocument(ROOT/'output/pdf'/item['pdf'])
        preview=document[0].render(scale=1).to_pil().convert('RGB')
        preview.thumbnail((340,480))
        x=95+index*455
        image.paste(preview,(x+40,345))
        text(draw,(x,849),f"{item['rows']} transfers",26,INK,650)
    text(draw,(95,922),f"{validation['accepted']:,} accepted rows | {validation['models']['hybrid']['full_batch_scored']} eligible scores | Declared CSV attachments",27,BLUE,650)
    frames.append(image)

    image,draw=frame('A graph. A decision. Evidence for review.', 'Dolos connects the data, the model and the reason.')
    text(draw,(90,390),'GraphSAGE to XGBoost',76,BLUE,800,'manrope')
    wrap(draw,(95,520),'The notebook compares one baseline with the final hybrid. The website uses synthetic teaching-model weights.',1600,36)
    wrap(draw,(95,715),'Prototype only. Not financial advice or proof of money laundering.',1580,36,MUTED)
    text(draw,(95,862),'dolos-web.vercel.app',42,INK,700,'manrope')
    frames.append(image)
    return frames


def clock(seconds):
    return f'{int(seconds)//3600:02d}:{int(seconds)//60%60:02d}:{int(seconds)%60:02d},000'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--voiceover', type=Path, help='Add your recorded narration to the existing silent master')
    args = parser.parse_args()
    if args.voiceover:
        if not args.voiceover.is_file() or not (OUT/'dolos-silent-draft.mp4').is_file():
            parser.error('The narration file and silent master must exist.')
        subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(),'-hide_banner','-loglevel','error','-y','-i',str(OUT/'dolos-silent-draft.mp4'),'-i',str(args.voiceover),'-map','0:v:0','-map','1:a:0','-c:v','copy','-c:a','aac','-af','apad','-t','114','-movflags','+faststart',str(OUT/'dolos-with-voiceover.mp4')],check=True)
        print(OUT/'dolos-with-voiceover.mp4')
        return
    OUT.mkdir(parents=True,exist_ok=True)
    data,_,_=demo_paths()
    mixed=analyze_demo('hybrid','mixed-network')
    ordinary=analyze_demo('hybrid','ordinary-payments')
    assert sum(item['score']>0 for item in mixed['scores'])==4
    assert len(ordinary['scores'])==8 and all(item['score']<=0 for item in ordinary['scores'])
    for item in mixed['scores']:
        assert abs(item['base_margin']+sum(part['contribution'] for part in item['component_contributions'])-item['score'])<1e-4
    validation=json.loads((ROOT/'output/pdf/validation.json').read_text())
    assert validation['models']['hybrid']['run_id']==mixed['run_id']
    (OUT/'evidence.json').write_text(json.dumps({'kind':'verified-local-data-animation','run_id':mixed['run_id'],'mixed':mixed,'ordinary':ordinary,'upload_validation':validation},indent=2)+'\n')
    frames=make_scenes(data,mixed,ordinary,validation)
    ffmpeg=imageio_ffmpeg.get_ffmpeg_exe()
    clips=[]
    for (start,end,stem,_),image in zip(SCENES,frames):
        image.save(OUT/(stem+'.png'))
        duration=end-start
        clip=OUT/(stem+'.mp4')
        subprocess.run([ffmpeg,'-hide_banner','-loglevel','error','-y','-loop','1','-i',str(OUT/(stem+'.png')),'-t',str(duration),'-r',str(FPS),'-vf',f'fade=t=in:st=0:d=0.35,fade=t=out:st={duration-.35}:d=0.35,format=yuv420p','-c:v','libx264','-preset','veryfast','-crf','20','-an','-movflags','+faststart',str(clip)],check=True)
        clips.append(clip)
    concat=OUT/'_clips.txt'
    concat.write_text(''.join("file '"+str(path)+"'\n" for path in clips))
    subprocess.run([ffmpeg,'-hide_banner','-loglevel','error','-y','-f','concat','-safe','0','-i',str(concat),'-c','copy','-movflags','+faststart',str(OUT/'dolos-silent-draft.mp4')],check=True)
    subtitles=[]
    for index,(start,end,_,narration) in enumerate(SCENES,1):
        # Separate subtitles into short sentence groups for readable playback.
        sentences=[sentence.strip()+'.' for sentence in narration.split('.') if sentence.strip()]
        total_words=sum(len(sentence.split()) for sentence in sentences)
        cursor=start
        for sentence in sentences:
            next_time=cursor+(end-start)*len(sentence.split())/total_words
            subtitles.append(f'{len(subtitles)+1}\n{clock(cursor)} --> {clock(next_time)}\n{sentence}\n')
            cursor=next_time
    (OUT/'narration.srt').write_text('\n'.join(subtitles))
    sheet=Image.new('RGB',(960*2,540*4),PAPER)
    for index,image in enumerate(frames):
        sheet.paste(image.resize((960,540)),((index%2)*960,(index//2)*540))
    sheet.save(OUT/'contact-sheet.png')
    manifest={'duration_seconds':114,'width':W,'height':H,'fps':FPS,'audio':'none; record the supplied narration','kind':'data animation, not a website recording','source_run_id':mixed['run_id'],'scenes':[{'start':start,'end':end,'file':stem+'.mp4'} for start,end,stem,_ in SCENES]}
    (OUT/'manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
    script=['# Narration draft','', 'Read this script over the silent draft. Speak at a steady rate. Do not add a claim about real-world accuracy.','']
    for start,end,_,narration in SCENES:
        script.extend([f'## {start//60}:{start%60:02d}–{end//60}:{end%60:02d}', '', narration, ''])
    (ROOT/'docs/video-script.md').write_text('\n'.join(script))
    print(json.dumps(manifest,indent=2))


if __name__=='__main__':
    main()
