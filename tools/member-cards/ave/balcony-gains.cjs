module.exports = function drawSplitBalcony({growth}) {
  const box=document.querySelector('.numbers');
  const table=box.querySelector('table.stats');
  const rows=[...table.tBodies[0].rows];
  const heads=[...table.tHead.rows[0].cells].map(c=>c.textContent);
  const totals=[...table.tFoot.rows[0].cells].map(c=>c.textContent);
  const stats=rows.map((row,i)=>({
    label:row.cells[0].textContent.trim(), color:row.querySelector('.dot').style.getPropertyValue('--stat'),
    data:growth.stats[i], displayed:[...row.cells].slice(1).map(c=>c.textContent)
  }));
  if (stats.reduce((a,s)=>a+s.data.full,0) !== growth.total) throw Error('Full total mismatch');
  const svg=document.createElementNS('http://www.w3.org/2000/svg','svg');
  const front = 72;
  svg.setAttribute('viewBox','0 0 360 '+(front+298));
  svg.setAttribute('aria-hidden','true');
  const frame = document.createElement('div');
  frame.className = 'stats';
  table.before(frame);
  table.className = 'seat-data';
  frame.append(table,svg);
  box.classList.add('stage-sections');
  const el=(type,attrs,parent=svg,content)=>{
    const node=document.createElementNS(svg.namespaceURI,type);
    for(const [k,v] of Object.entries(attrs)) node.setAttribute(k,v);
    if(content!==undefined) node.textContent=content;
    parent.append(node); return node;
  };
  const txt=(x,y,value,cls,anchor='middle')=>el('text',{x,y,class:cls,'text-anchor':anchor},svg,value);
  const path=(d,fill='none',stroke='none',w=1)=>el('path',{d,fill,stroke,'stroke-width':w});
  const defs=el('defs',{});
  const tile=el('g',{id:'ave-chair',fill:'none',stroke:'currentColor','stroke-linecap':'round','stroke-linejoin':'round'},defs);
  el('rect',{x:-3.45,y:-2.75,width:6.9,height:4.4,rx:1.3,fill:'currentColor',stroke:'none'},tile);
  el('path',{d:'M-2.65 -1.55q2.6-.65 5.1 0',stroke:'#fff2dd',opacity:.46,'stroke-width':.62},tile);
  el('path',{d:'M-4.8 -.6v2.3q0 1.5 1.6 1.8 3.1.6 6.4 0Q4.8 3.2 4.8 1.7V-.6','stroke-width':1.05},tile);
  const stall=el('g',{id:'ave-stall',fill:'none','stroke-linecap':'round','stroke-linejoin':'round'},defs);
  el('path',{d:'M-4.3 -1.4q4.3-1.4 8.6 0l-.4 3.3q-3.9 1.2-7.8 0Z',fill:'currentColor'},stall);
  el('path',{d:'M-3.5 -.95q3.5-.95 7 0',stroke:'#fff1d7',opacity:.62,'stroke-width':.66},stall);
  el('path',{d:'M-5.4 -2.4l.3 5.2q5.1 3.1 10.2 0l.3-5.2',stroke:'currentColor','stroke-width':1.7},stall);
  el('path',{d:'M-3.5 3.4q3.5 1.4 7 0',stroke:'#ffebcf','stroke-width':.85},stall);
  const burgundy=el('linearGradient',{id:'velvet-ink',x1:'0',x2:'0',y1:'0',y2:'1'},defs);
  for (const [offset,color] of [[0,'#733b4d'],[.07,'#6c3245'],[.41,'#592c3e'],[1,'#422334']]) {
    el('stop',{offset,'stop-color':color},burgundy);
  }
  const brass=el('linearGradient',{id:'stage-brass',x1:0,y1:.8,x2:1,y2:.18},defs);
  for (const [offset,color] of [[0,'#a88568'],[.18,'#d7bc99'],[.48,'#ecdbc1'],[.8,'#ddc4a4'],[1,'#af8c6c']]) {
    el('stop',{offset,'stop-color':color},brass);
  }

  const labelX=11, start=90, pitch=88, end=start+3*pitch;
  const x=stats.map((_,i)=>start+(i+.5)*pitch);
  const center=x[1], radius=(end-start)/2;
  txt(labelX,34,heads[0],'title','start');
  // The stage opens directly over the three attribute blocks.
  path('M'+start+' 8H'+end+'l-9 34Q'+center+' 63 '+(start+9)+' 42Z','url(#stage-brass)','#9b847b',.85);
  path('M'+(start+4)+' 11H'+(end-4),'none','#fff8ee',1);
  const caption=txt(0,0,'满培养综合力','stage-label','start');
  const amount=txt(0,0,totals[3],'headline','start');
  const gap=9, captionBox=caption.getBBox(), amountBox=amount.getBBox();
  const captionLeft=center-(captionBox.width+gap+amountBox.width)/2;
  caption.setAttribute('x',captionLeft-captionBox.x);
  amount.setAttribute('x',captionLeft+captionBox.width+gap-amountBox.x);
  for(const [part,bounds] of [[caption,captionBox],[amount,amountBox]]) {
    part.setAttribute('y',28-bounds.y-bounds.height/2);
  }
  const lip='M'+(start+9)+' 44Q'+center+' 65 '+(end-9)+' 44';
  path(lip,'none','#8b7470',7);
  let used=0;
  for(const stat of stats) {
    const piece=path(lip,'none',stat.color,4.3);
    piece.setAttribute('pathLength',growth.total);
    piece.setAttribute('stroke-dasharray',stat.data.full+' '+growth.total);
    piece.setAttribute('stroke-dashoffset',-used);
    used+=stat.data.full;
  }

  const heightAt=(pos,y)=>y+8*(1-((pos-center)/radius)**2);
  const arc=(a,b,y)=>{
    const mid=(a+b)/2;
    return 'Q'+mid+' '+(2*heightAt(mid,y)-(heightAt(a,y)+heightAt(b,y))/2)+' '+b+' '+heightAt(b,y);
  };
  function sector(col,y1,y2,w1,w2,fill,edge) {
    const a=x[col]-w1/2, b=x[col]+w1/2, c=x[col]+w2/2, d=x[col]-w2/2;
    return path('M'+a+' '+heightAt(a,y1)+arc(a,b,y1)+'L'+c+' '+heightAt(c,y2)+arc(c,d,y2)+'Z',fill,edge,.65);
  }
  function chairs(col,y,count,size,color,symbol='#ave-chair') {
    for(let j=0;j<count;j++) {
      const pos=x[col]+((j+.5)/count-.5)*(pitch-12);
      const angle=Math.atan(-16*(pos-center)/(radius*radius))*180/Math.PI;
      el('use',{href:symbol,transform:'translate('+pos+' '+heightAt(pos,y)+') rotate('+angle+') scale('+size+')',color});
    }
  }
  if (stats.some(({data}) => data.base+data.trainingBonus+data.awakeningBonus!==data.full)) {
    throw Error('Gains do not reconcile with full stats');
  }

  const tints=['#cc95a5','#94bfcb','#ccb284'];
  const training='特训 '+(growth.trainingStage-1)+' 次';
  const awakening='觉醒 '+(growth.awakeningStage-1)+' 次';
  table.createCaption().textContent='满培养：'+training+'，'+awakening;
  txt(labelX,front+27,'满培养','row-label','start');
  txt(labelX,front+51,training,'growth-count','start');
  txt(labelX,front+74,awakening,'growth-count','start');
  const highest=Math.max(...stats.map(s=>s.data.full));
  stats.forEach((s,i)=>{
    sector(i,front,front+83,80,84,'url(#velvet-ink)','#593342');
    chairs(i,front+13,4,1.16,s.color,'#ave-stall');
    txt(x[i],front+45,s.label,'section-name');
    txt(x[i],front+73,s.displayed[2],'front-value');
    if (s.data.full===highest) {
      path('M'+(x[i]-11)+' '+(front+80)+'h22','none',s.color,1.6).setAttribute('stroke-linecap','round');
    }
  });
  [{base:front+99,width:83,shade:'#493640',head:heads[2],slot:1,total:totals[2],chairs:5},
   {base:front+182,width:84,shade:'#39303b',head:heads[1],slot:0,total:totals[1],chairs:7}].forEach(row=>{
    const numberBaseline=row.base+60;
    const totalRight=start-9;
    txt(labelX,row.base+25,row.head,'row-label','start');
    txt(totalRight,numberBaseline,row.total,'row-total','end');
    stats.forEach((s,i)=>{
      sector(i,row.base,row.base+70,row.width,row.width+2,row.shade,'#937680');
      chairs(i,row.base+13,row.chairs,row.chairs===5?.97:.8,tints[i]);
      txt(x[i],numberBaseline,s.displayed[row.slot],'value');
    });
    path('M'+labelX+' '+(row.base+32)+'H'+(end-5),'none','#c6a2a2',.7)
      .setAttribute('opacity','.48');
  });
  // The outer rail stops at the two aisles; steps open onto the back of the hall.
  const rail=front+270, doorHalf=7;
  x.forEach((cx,i)=>{
    const a=cx-pitch/2+(i===0?1:doorHalf), b=cx+pitch/2-(i===2?1:doorHalf);
    const edge='M'+a+' '+heightAt(a,rail)+arc(a,b,rail);
    path(edge,'none','#a69b9f',1.1);
    path('M'+a+' '+heightAt(a,rail+2.6)+arc(a,b,rail+2.6),'none','#c9c0bd',.65);
  });
  for (const [outer,turn] of [[start+1,1],[end-1,-1]]) {
    path('M'+(outer+turn*2)+' '+heightAt(outer,rail-9)+'l'+(-turn*2)+' 4v5','none','#a69b9f',1);
  }
  for (const aisle of [start+pitch,start+2*pitch]) {
    const base=heightAt(aisle,rail);
    for(let step=0;step<3;step++) {
      const half=doorHalf-2+step;
      path('M'+(aisle-half)+' '+(base+3.6*step)+'h'+(2*half),'none','#a69b9f',.9);
    }
  }
};
