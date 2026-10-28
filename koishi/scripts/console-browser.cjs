// Development acceptance only. Pass installed npm asset directory, Playwright package and screenshot directory.
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path')
const {chromium}=require(process.argv[2]),output=path.resolve(process.argv[3])
async function verifyReconnect(page){
  const nav=()=>page.getByRole('navigation',{name:'Taki 工作页面'})
  const reconnect=async failures=>{
    const before=await page.evaluate(()=>window.fixtureDocument),url=page.url()
    await page.evaluate(n=>{
      window.fixtureFailures=n
      window.fixtureSockets.filter(s=>s.readyState===1).forEach(s=>s.close(4000,'isolated reconnect check'))
    },failures)
    await page.waitForFunction(before=>typeof window.fixtureDocument==='string'&&window.fixtureDocument!==before,before,{timeout:30000})
    await page.waitForURL(url)
    await page.getByLabel('管理员密码').fill('fixture-console-password-000000')
    await page.getByRole('button',{name:'登录',exact:true}).click()
    await page.getByRole('heading',{name:'总览',exact:true}).waitFor()
  }
  await nav().getByText('设置',{exact:true}).click()
  const toggle=page.getByLabel('允许私聊',{exact:true}),initial=await toggle.isChecked()
  await toggle.setChecked(!initial)
  await page.getByLabel('模型 ID',{exact:true}).fill('fixture-unsaved-reconnect-model')
  await page.getByLabel('新密钥',{exact:true}).fill('fixture-never-persist-this-key')
  await nav().getByText('群管理',{exact:true}).click();await page.locator('.group-entry').first().click()
  await page.getByLabel('群备注',{exact:true}).fill('重连保留草稿')
  const stored=await page.evaluate(()=>sessionStorage.getItem('taki.console.recovery.v1'))
  assert.ok(stored.includes('重连保留草稿')&&stored.includes('fixture-unsaved-reconnect-model'))
  assert.ok(!stored.includes('fixture-never-persist-this-key'))
  await reconnect(2)
  await nav().getByText('设置',{exact:true}).click()
  assert.equal(await toggle.isChecked(),!initial)
  assert.equal(await page.getByLabel('模型 ID',{exact:true}).inputValue(),'fixture-unsaved-reconnect-model')
  assert.equal(await page.getByLabel('新密钥',{exact:true}).inputValue(),'')
  await nav().getByText('群管理',{exact:true}).click()
  assert.equal(await page.getByLabel('群备注',{exact:true}).inputValue(),'重连保留草稿')
  await page.getByRole('button',{name:'保存群策略',exact:true}).click()
  await page.getByText('群策略已保存。新查询使用新规则，在途查询继续完成。',{exact:true}).waitFor()
  await nav().getByText('设置',{exact:true}).click()
  await page.getByRole('button',{name:'放弃 AI 草稿并刷新',exact:true}).click()
  await page.getByRole('button',{name:'保存设置',exact:true}).click()
  await page.getByText('已保存生效；在途查询继续完成。',{exact:true}).waitFor()
  assert.equal(await page.locator('.draft-dot').count(),0)
  await reconnect(0)
  await nav().getByText('设置',{exact:true}).click()
  assert.equal(await toggle.isChecked(),!initial)
  await page.screenshot({path:path.join(output,'reconnected-drafts-saved.png'),fullPage:true})
  return {passed:true,actualWebSocketReconnect:true,failedConnectionsBeforeSuccess:2,noDraftReconnect:true,
    fullUrlPreserved:true,reauthentication:true,globalGroupModelDraftsPreserved:true,
    unsubmittedSecretNotStored:true,groupAndGlobalSaveAfterReconnect:true}
}
async function verifySettingsScroll(page){
  const layout=page.locator('.layout-main'),save=page.getByRole('button',{name:'保存设置',exact:true})
  const toggle=page.getByLabel('允许私聊',{exact:true})
  const position=async()=>{
    const bounds=await layout.boundingBox(),box=await save.boundingBox()
    const hit=await page.evaluate(({x,y})=>document.elementFromPoint(x,y)?.closest('button')?.textContent.trim(),{x:box.x+box.width/2,y:box.y+box.height/2})
    return {bounds,box,ready:box.y>=bounds.y&&box.y+box.height<=bounds.y+bounds.height&&hit==='保存设置'}
  }
  await page.setViewportSize({width:1280,height:720})
  await page.getByRole('navigation',{name:'Taki 工作页面'}).getByRole('button',{name:'设置',exact:true}).click()
  const initial=await toggle.isChecked();await toggle.setChecked(!initial)
  await layout.evaluate(e=>e.scrollTop=0)
  let {bounds,box}=await position()
  assert.ok(await layout.evaluate(e=>e.scrollHeight>e.clientHeight),'fixture must include content below the visible viewport')
  await page.mouse.move(900,500);await page.mouse.wheel(0,300);await page.waitForTimeout(300)
  assert.ok(await layout.evaluate(e=>e.scrollTop)>100,'mouse wheel must scroll the Taki page')
  let current=await position()
  for(let attempt=0;attempt<10&&!current.ready;attempt++){
    await page.mouse.wheel(0,current.box.y<current.bounds.y+50?-120:120);await page.waitForTimeout(150);current=await position()
  }
  assert.ok(current.ready,'save button must be visible and clickable after scrolling')
  box=current.box
  await page.screenshot({path:path.join(output,'settings-scroll-desktop.png')})
  await page.mouse.click(box.x+box.width/2,box.y+box.height/2)
  await page.getByText('已保存生效；在途查询继续完成。',{exact:true}).waitFor()
  await page.getByText('当前显示已载入的全局设置。',{exact:true}).waitFor()

  await page.setViewportSize({width:320,height:640})
  await toggle.setChecked(initial);await layout.evaluate(e=>e.scrollTop=0)
  const touch=await page.context().newCDPSession(page)
  current=await position()
  for(let attempt=0;attempt<10&&(attempt===0||!current.ready);attempt++){
    const upwards=current.box.y>=current.bounds.y+50,start=upwards?480:250,step=upwards?-40:40
    await touch.send('Input.dispatchTouchEvent',{type:'touchStart',touchPoints:[{x:220,y:start}]})
    for(let n=1;n<=4;n++){await touch.send('Input.dispatchTouchEvent',{type:'touchMove',touchPoints:[{x:220,y:start+step*n}]});await page.waitForTimeout(35)}
    await touch.send('Input.dispatchTouchEvent',{type:'touchEnd',touchPoints:[]});await page.waitForTimeout(300)
    current=await position()
  }
  assert.ok(await layout.evaluate(e=>e.scrollTop)>100,'touch must scroll the Taki page')
  assert.ok(current.ready,'save button must be visible and clickable after touch scrolling')
  box=current.box
  await page.screenshot({path:path.join(output,'settings-scroll-mobile.png')})
  await page.touchscreen.tap(box.x+box.width/2,box.y+box.height/2)
  await page.getByText('已保存生效；在途查询继续完成。',{exact:true}).waitFor()
  await page.getByText('当前显示已载入的全局设置。',{exact:true}).waitFor()
  assert.equal(await toggle.isChecked(),initial)
  await touch.detach()
}
async function main(){
  fs.mkdirSync(output,{recursive:true})
  const browser=await chromium.launch({executablePath:process.argv[4],headless:true})
  const page=await browser.newPage({viewport:{width:1280,height:960},hasTouch:true}),errors=[],failed=[]
  const manager=process.argv[5]?path.resolve(process.argv[5]):null
  page.on('pageerror',e=>errors.push(e.message))
  page.on('response',r=>{if(r.status()>=400)failed.push([r.status(),r.url()])})
  await page.route('**/*',route=>new URL(route.request().url()).hostname==='127.0.0.1'?route.continue():route.abort())
  const reconnect=process.env.TAKI_RECONNECT_ONLY==='1'
  if(reconnect){
    page.on('dialog',dialog=>dialog.accept())
    await page.addInitScript(()=>{
      window.fixtureDocument=crypto.randomUUID();window.fixtureSockets=[];window.fixtureFailures=0
      const Native=window.WebSocket
      window.WebSocket=class extends Native {constructor(url,...args){
        if(window.fixtureFailures>0){window.fixtureFailures--;url='ws://127.0.0.1:15143/status'}
        super(url,...args);window.fixtureSockets.push(this)
      }}
    })
  }
  try{
    await page.goto('http://127.0.0.1:15142/taki'+(reconnect?'?reconnect_fixture=1#detail':''),{waitUntil:'networkidle'})
    await page.screenshot({path:path.join(output,'login-desktop.png'),fullPage:true})
    await page.getByLabel('管理员密码').fill('fixture-console-password-000000')
    await page.getByRole('button',{name:'登录',exact:true}).click()
    await page.getByRole('heading',{name:'总览',exact:true}).waitFor()
    if(reconnect){
      const result=await verifyReconnect(page);assert.deepEqual(errors,[]);assert.deepEqual(failed,[])
      fs.writeFileSync(path.join(output,'reconnect.json'),JSON.stringify({...result,errors,failed},null,2));return
    }
    if(process.env.TAKI_SCROLL_ONLY==='1'){
      await verifySettingsScroll(page)
      assert.deepEqual(errors,[]);assert.deepEqual(failed,[])
      fs.writeFileSync(path.join(output,'scroll.json'),JSON.stringify({passed:true,mouse:true,touch:true,installedAssets:true},null,2))
      return
    }
    const nav=page.getByRole('navigation',{name:'Taki 工作页面'})
    await page.locator('.overview-stat').first().getByText('1',{exact:true}).waitFor()
    assert.equal(await nav.getByText('总览',{exact:true}).locator('..').getAttribute('aria-current'),'page')
    await page.getByText('数据来源与采集状态 · 排查缺图、旧数据时查看',{exact:true}).click()
    await page.getByRole('heading',{name:'网关采集健康',exact:true}).waitFor()
    await page.getByText('数据来源与采集状态 · 排查缺图、旧数据时查看',{exact:true}).click()
    await page.screenshot({path:path.join(output,'overview-desktop.png'),fullPage:true})
    await nav.getByText('设置',{exact:true}).click()
    const toggle=page.getByLabel('允许私聊',{exact:true})
    const initial=await toggle.isChecked();await toggle.setChecked(!initial)
    await page.getByText('有未保存的全局设置',{exact:true}).waitFor()
    await page.waitForTimeout(16000)
    assert.equal(await toggle.isChecked(),!initial,'poll must preserve draft')
    await nav.getByText('查询记录',{exact:true}).click()
    await page.getByRole('combobox',{name:'发送状态'}).selectOption('failed')
    await page.getByRole('button',{name:'最近 24 小时',exact:true}).click()
    await page.getByText(/当前 0 条/).waitFor()
    await page.getByRole('combobox',{name:'发送状态'}).selectOption('')
    await page.getByRole('button',{name:'最近 24 小时',exact:true}).click()
    await page.getByText(/当前 1 条/).waitFor()
    await page.getByRole('button',{name:/^查看请求 /}).first().click()
    await page.getByRole('heading',{name:'查询详情',exact:true}).waitFor()
    await page.getByText('查看完整记录与阶段耗时',{exact:true}).click()
    await page.screenshot({path:path.join(output,'request-desktop.png'),fullPage:true})
    await nav.getByText('群管理',{exact:true}).click()
    await page.locator('.group-entry').first().waitFor()
    if(manager){
      await page.locator('.group-entry').first().click()
      await page.getByLabel('群备注',{exact:true}).fill('尚未保存甲')
      const other=await browser.newPage()
      await other.route('**/*',route=>new URL(route.request().url()).hostname==='127.0.0.1'?route.continue():route.abort())
      await other.goto('http://127.0.0.1:15142/taki',{waitUntil:'networkidle'})
      await other.getByLabel('管理员密码').fill('fixture-console-password-000000')
      await other.getByRole('button',{name:'登录',exact:true}).click()
      await other.getByRole('heading',{name:'总览',exact:true}).waitFor()
      await other.getByRole('navigation',{name:'Taki 工作页面'}).getByText('群管理',{exact:true}).click()
      await other.locator('.group-entry').first().click()
      await other.getByLabel('群备注',{exact:true}).fill('服务器已保存乙')
      await other.getByRole('button',{name:'保存群策略',exact:true}).click()
      await other.getByText('群策略已保存。新查询使用新规则，在途查询继续完成。',{exact:true}).waitFor()
      await page.bringToFront()
      await page.getByRole('button',{name:'保存群策略',exact:true}).click()
      await page.getByText('群策略版本冲突或保存未确认；草稿仍保留。',{exact:true}).waitFor()
      assert.equal(await page.getByLabel('群备注',{exact:true}).inputValue(),'尚未保存甲')
      assert.equal(JSON.parse(fs.readFileSync(path.join(manager,'group-policies.v1.json'))).groups['fixture-group'].label,'服务器已保存乙')
      await page.screenshot({path:path.join(output,'group-conflict-desktop.png'),fullPage:true})
      await page.getByRole('button',{name:'放弃群草稿并载入服务器值',exact:true}).click()
      assert.equal(await page.getByLabel('群备注',{exact:true}).inputValue(),'服务器已保存乙')
      await other.close()
    }
    await nav.getByText('数据来源',{exact:true}).click()
    await page.getByRole('heading',{name:'同步与修复',exact:true}).waitFor()
    await page.getByRole('button',{name:'检查并同步',exact:true}).waitFor({state:'visible'})
    if(manager){
      await page.waitForFunction(()=>[...document.querySelectorAll('button')].some(e=>e.textContent.trim()==='检查并同步'&&!e.disabled),null,{timeout:20000})
      await page.getByRole('button',{name:'检查并同步',exact:true}).click()
      await page.getByText(/^操作已受理：/).waitFor()
    }
    await nav.getByText('设置',{exact:true}).click()
    await page.getByRole('heading',{name:'模型与额度',exact:true}).waitFor()
    await page.getByRole('heading',{name:'维护与恢复',exact:true}).waitFor()
    assert.equal(await page.getByRole('button',{name:'进入维护',exact:true}).isDisabled(),true,'a dirty draft must block maintenance')
    await page.setViewportSize({width:320,height:900})
    await page.emulateMedia({colorScheme:'dark'})
    // Explicit host theme: OS preference alone need not switch a saved Koishi theme.
    await page.evaluate(()=>document.documentElement.classList.add('dark'))
    assert.notEqual(await page.locator('.taki-console').evaluate(e=>getComputedStyle(e).getPropertyValue('--bg1').trim()),'#f9f9fa')
    assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=window.innerWidth),true,'320px horizontal overflow')
    await page.keyboard.press('Tab')
    assert.equal(await page.evaluate(()=>document.activeElement!==document.body),true)
    await page.screenshot({path:path.join(output,'groups-320-dark.png'),fullPage:true})
    if(await page.getByRole('dialog',{name:'群详情'}).count())await page.getByRole('button',{name:'关闭群详情'}).tap()
    await page.getByRole('button',{name:'打开导航'}).tap()
    const navigationDialog=page.getByRole('dialog',{name:'导航',exact:true});await navigationDialog.waitFor()
    assert.equal(await navigationDialog.evaluate(e=>e.contains(document.activeElement)),true,'opening navigation must move keyboard focus inside')
    await navigationDialog.getByText('总览',{exact:true}).focus();await page.keyboard.press('Shift+Tab')
    assert.equal(await navigationDialog.evaluate(e=>e.contains(document.activeElement)),true,'navigation must retain keyboard focus')
    await page.keyboard.press('Escape')
    assert.equal(await page.getByRole('button',{name:'打开导航'}).evaluate(e=>e===document.activeElement),true,'closing navigation must return focus')
    await page.getByRole('button',{name:'打开导航'}).tap();await nav.getByText('查询记录',{exact:true}).tap()
    if(await page.getByRole('dialog',{name:'查询详情'}).count())await page.getByRole('button',{name:'关闭查询详情'}).tap()
    await page.getByRole('button',{name:/^查看请求 /}).first().tap()
    const requestDialog=page.getByRole('dialog',{name:'查询详情'});await requestDialog.waitFor()
    const [download]=await Promise.all([page.waitForEvent('download'),requestDialog.getByRole('button',{name:'导出此请求诊断',exact:true}).click()])
    const diagnostic=JSON.parse(fs.readFileSync(await download.path(),'utf8'))
    assert.equal(diagnostic.schema,'taki-diagnostic-v1');assert.equal(diagnostic.scope,'one_request','mobile export must contain only the selected request')
    await page.getByRole('button',{name:'关闭查询详情'}).focus();await page.keyboard.press('Shift+Tab')
    assert.equal(await requestDialog.evaluate(e=>e.contains(document.activeElement)),true,'mobile detail must retain keyboard focus')
    await page.keyboard.press('Escape');assert.equal(await requestDialog.count(),0,'Escape must close mobile detail')
    await page.getByRole('button',{name:'打开导航'}).tap()
    await nav.getByText('总览',{exact:true}).tap()
    if(manager){
      const marker=path.join(manager,'fixture-backend-offline')
      try{
        fs.writeFileSync(marker,'test')
        await page.getByText('连接异常：以下保留数据可能已陈旧。',{exact:true}).waitFor({timeout:22000})
        await page.locator('.overview-stat').first().getByText('1',{exact:true}).waitFor()
        await page.getByRole('button',{name:'打开导航'}).tap();await nav.getByText('设置',{exact:true}).tap()
        assert.equal(await toggle.isChecked(),!initial,'offline must preserve draft')
        await page.screenshot({path:path.join(output,'backend-offline-draft-320.png'),fullPage:true})
      }finally{fs.rmSync(marker,{force:true})}
      await page.evaluate(()=>document.dispatchEvent(new Event('visibilitychange')))
      await page.getByText('连接已恢复，草稿未被覆盖。',{exact:true}).waitFor({timeout:22000})
    }
    if(!manager){await page.getByRole('button',{name:'打开导航'}).tap();await nav.getByText('设置',{exact:true}).tap()}
    await page.getByRole('button',{name:'保存设置',exact:true}).click()
    await page.getByText('已保存生效；在途查询继续完成。',{exact:true}).waitFor()
    assert.deepEqual(errors,[])
    assert.deepEqual(failed,[])
    fs.writeFileSync(path.join(output,'browser.json'),JSON.stringify({passed:true,errors,failed,viewport:[1280,320],installedAssets:true,groupConflict:!!manager,backendFailureDraft:!!manager},null,2))
  }finally{await browser.close()}
}
main().catch(e=>{console.error(e);process.exitCode=1})
