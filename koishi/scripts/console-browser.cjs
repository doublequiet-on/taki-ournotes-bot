// Development acceptance only. Pass installed npm asset directory, Playwright package and screenshot directory.
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path')
const {chromium}=require(process.argv[2]),output=path.resolve(process.argv[3])
async function verifyConversation(page,browser){
  const open=async target=>{const menu=target.getByRole('button',{name:'打开导航'});if(await menu.isVisible())await menu.click();await target.getByRole('navigation',{name:'Taki 工作页面'}).getByText('群管理',{exact:true}).click();await target.locator('.group-entry').first().click();await target.getByRole('button',{name:'交流与记忆',exact:true}).click();await target.getByText('当前显示已保存的交流设置',{exact:true}).waitFor()}
  const login=async target=>{await target.goto('http://127.0.0.1:15142/taki',{waitUntil:'networkidle'});await target.getByLabel('管理员密码').fill('fixture-console-password-000000');await target.getByRole('button',{name:'登录',exact:true}).click();await target.getByRole('heading',{name:'总览',exact:true}).waitFor()}
  const saved=async target=>{await target.getByText('交流设置已保存并确认生效。',{exact:true}).waitFor();await target.getByText('当前显示已保存的交流设置',{exact:true}).waitFor()}
  await open(page)
  const settings=page.locator('.conversation-settings'),list=settings.locator('.conversation-exclusions')
  await page.getByLabel('从近期用户选择',{exact:true}).selectOption('fixture-recent-user')
  await page.getByRole('button',{name:'添加到名单',exact:true}).click()
  await page.getByLabel('平台用户 ID',{exact:true}).fill('manual-user-'+'x'.repeat(120))
  await page.getByLabel('用户备注',{exact:true}).fill('手动用户 · 名单备注与很长的平台 ID 会完整换行')
  await page.getByRole('button',{name:'添加到名单',exact:true}).click()
  assert.equal(await list.locator('li').count(),2)
  await page.getByLabel('从近期用户选择',{exact:true}).selectOption('fixture-recent-user')
  await page.getByRole('button',{name:'添加到名单',exact:true}).click()
  await page.getByText('这位用户已在名单中。',{exact:true}).waitFor()
  assert.equal(await list.locator('li').count(),2)
  await page.getByLabel('启用免 @ 续聊',{exact:true}).uncheck()
  await page.getByLabel('续聊窗口（秒）',{exact:true}).fill('240')
  await page.getByRole('button',{name:'保存交流设置',exact:true}).click();await saved(page)
  await page.getByRole('button',{name:'刷新交流设置',exact:true}).click()
  assert.equal(await page.getByLabel('启用免 @ 续聊',{exact:true}).isChecked(),false)
  await login(page);await open(page)
  assert.equal(await list.locator('li').count(),2,'saved list persists across browser reload')
  assert.equal(await page.getByLabel('续聊窗口（秒）',{exact:true}).inputValue(),'240')
  await page.getByLabel('续聊窗口（秒）',{exact:true}).fill('300')
  const other=await browser.newPage({viewport:{width:1280,height:960}})
  await other.route('**/*',route=>new URL(route.request().url()).hostname==='127.0.0.1'?route.continue():route.abort())
  await login(other);await open(other)
  await other.getByLabel('合并消息等待（秒）',{exact:true}).fill('1.8')
  await other.getByRole('button',{name:'保存交流设置',exact:true}).click();await saved(other)
  await page.getByRole('button',{name:'刷新交流设置',exact:true}).click()
  await page.getByText('服务器设置已变化或保存未确认，草稿保留。请核对当前值后继续。',{exact:true}).waitFor()
  assert.equal(await page.getByLabel('续聊窗口（秒）',{exact:true}).inputValue(),'300')
  await page.getByRole('button',{name:'放弃交流草稿并刷新',exact:true}).click()
  await page.getByText('当前显示已保存的交流设置',{exact:true}).waitFor()
  assert.equal(await page.getByLabel('合并消息等待（秒）',{exact:true}).inputValue(),'1.8')
  await other.close()
  await page.getByLabel('启用免 @ 续聊',{exact:true}).check()
  await page.getByRole('button',{name:'保存交流设置',exact:true}).click();await saved(page)
  await settings.getByText(/命中不主动搭话名单/).waitFor()
  await settings.getByText(/知识库后继续补查 Haneoka/).waitFor()
  await settings.evaluate(e=>e.scrollIntoView({block:'start'}));await page.locator('.layout-main').evaluate(e=>e.scrollTop-=90)
  await page.screenshot({path:path.join(output,'conversation-desktop.png')})
  await settings.getByRole('heading',{name:'近期交流记录',exact:true}).scrollIntoViewIfNeeded()
  await page.screenshot({path:path.join(output,'conversation-logs-desktop.png')})
  const mobile=await browser.newPage({viewport:{width:320,height:900},hasTouch:true}),mobileErrors=[],mobileFailed=[]
  mobile.on('pageerror',error=>mobileErrors.push(error.message));mobile.on('response',response=>{if(response.status()>=400)mobileFailed.push(response.url())})
  await mobile.route('**/*',route=>new URL(route.request().url()).hostname==='127.0.0.1'?route.continue():route.abort())
  await login(mobile)
  await mobile.getByRole('button',{name:'切换深浅主题',exact:true}).click()
  await open(mobile)
  await mobile.getByRole('button',{name:'移除 近期用户（合成验收）',exact:true}).click()
  await mobile.getByRole('button',{name:'保存交流设置',exact:true}).tap()
  await saved(mobile)
  const mobileSettings=mobile.locator('.conversation-settings')
  assert.equal(await mobileSettings.locator('.conversation-exclusions li').count(),1,'mobile removal persists')
  assert.equal(await mobile.evaluate(()=>document.documentElement.scrollWidth<=window.innerWidth),true,'mobile document must not overflow')
  assert.equal(await mobileSettings.evaluate(e=>e.scrollWidth<=e.clientWidth),true,'mobile settings must not overflow')
  await mobileSettings.evaluate(e=>e.scrollIntoView({block:'start'}))
  await mobile.screenshot({path:path.join(output,'conversation-mobile-dark.png')})
  await mobileSettings.getByRole('heading',{name:'连续资料任务',exact:true}).scrollIntoViewIfNeeded()
  await mobile.screenshot({path:path.join(output,'conversation-mobile-log.png')})
  assert.deepEqual(mobileErrors,[]);assert.deepEqual(mobileFailed,[]);await mobile.close()
  await page.getByRole('button',{name:'关闭群详情',exact:true}).click()
  await page.getByRole('navigation',{name:'Taki 工作页面'}).getByText('请求记录',{exact:true}).click()
  await page.getByRole('button',{name:/^查看请求 /}).first().click()
  await page.getByRole('heading',{name:'交流处理记录',exact:true}).waitFor()
  await page.getByRole('heading',{name:'交流处理记录',exact:true}).scrollIntoViewIfNeeded()
  await page.screenshot({path:path.join(output,'conversation-request-log.png')})
  return {passed:true,syntheticData:true,realAdminSaveReadback:true,recentUserSelection:true,manualId:true,duplicatePrevented:true,reloadPersisted:true,conflictingDraftPreserved:true,mobileRemoval:true,desktopAndMobile:true,groupAndRequestLogs:true}
}
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
  await nav().getByText('模型配置',{exact:true}).click()
  await page.getByLabel('模型 ID',{exact:true}).fill('fixture-unsaved-reconnect-model')
  await page.getByLabel('新密钥',{exact:true}).fill('fixture-never-persist-this-key')
  await nav().getByText('群管理',{exact:true}).click();await page.locator('.group-entry').first().click()
  await page.getByRole('button',{name:'查询策略',exact:true}).click()
  await page.getByLabel('群备注',{exact:true}).fill('重连保留草稿')
  const stored=await page.evaluate(()=>sessionStorage.getItem('taki.console.recovery.v1'))
  assert.ok(stored.includes('重连保留草稿')&&stored.includes('fixture-unsaved-reconnect-model'))
  assert.ok(!stored.includes('fixture-never-persist-this-key'))
  await reconnect(2)
  await nav().getByText('设置',{exact:true}).click()
  assert.equal(await toggle.isChecked(),!initial)
  await nav().getByText('模型配置',{exact:true}).click()
  assert.equal(await page.getByLabel('模型 ID',{exact:true}).inputValue(),'fixture-unsaved-reconnect-model')
  assert.equal(await page.getByLabel('新密钥',{exact:true}).inputValue(),'')
  await nav().getByText('群管理',{exact:true}).click()
  assert.equal(await page.getByLabel('群备注',{exact:true}).inputValue(),'重连保留草稿')
  await page.getByRole('button',{name:'保存群策略',exact:true}).click()
  await page.getByText('群策略已保存。新查询使用新规则，在途查询继续完成。',{exact:true}).waitFor()
  await nav().getByText('设置',{exact:true}).click()
  await nav().getByText('模型配置',{exact:true}).click()
  await page.getByRole('button',{name:'放弃 AI 草稿并刷新',exact:true}).click()
  await nav().getByText('设置',{exact:true}).click()
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
  // The desktop settings grid is shorter. Use a short viewport to exercise wheel scrolling.
  await page.setViewportSize({width:1280,height:480})
  await page.getByRole('navigation',{name:'Taki 工作页面'}).getByRole('button',{name:'设置',exact:true}).click()
  const initial=await toggle.isChecked();await toggle.setChecked(!initial)
  await layout.evaluate(e=>e.scrollTop=0)
  let {bounds,box}=await position()
  assert.ok(await layout.evaluate(e=>e.scrollHeight>e.clientHeight),'fixture must include content below the visible viewport')
  await page.mouse.move(900,350);await page.mouse.wheel(0,300);await page.waitForTimeout(300)
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
    if(process.env.TAKI_CONVERSATION_ONLY==='1'){
      const result=await verifyConversation(page,browser);assert.deepEqual(errors,[]);assert.deepEqual(failed,[])
      fs.writeFileSync(path.join(output,'conversation.json'),JSON.stringify({...result,errors,failed},null,2));return
    }
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
    await nav.getByText('请求记录',{exact:true}).click()
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
      await page.getByRole('button',{name:'查询策略',exact:true}).click()
      await page.getByLabel('群备注',{exact:true}).fill('尚未保存甲')
      const other=await browser.newPage()
      await other.route('**/*',route=>new URL(route.request().url()).hostname==='127.0.0.1'?route.continue():route.abort())
      await other.goto('http://127.0.0.1:15142/taki',{waitUntil:'networkidle'})
      await other.getByLabel('管理员密码').fill('fixture-console-password-000000')
      await other.getByRole('button',{name:'登录',exact:true}).click()
      await other.getByRole('heading',{name:'总览',exact:true}).waitFor()
      await other.getByRole('navigation',{name:'Taki 工作页面'}).getByText('群管理',{exact:true}).click()
      await other.locator('.group-entry').first().click()
      await other.getByRole('button',{name:'查询策略',exact:true}).click()
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
    await nav.getByText('模型配置',{exact:true}).click()
    await page.getByRole('heading',{name:'模型参数',exact:true}).waitFor()
    await nav.getByText('运行维护',{exact:true}).click()
    await page.getByRole('heading',{name:'维护与恢复',exact:true}).waitFor()
    assert.equal(await page.getByRole('button',{name:'进入维护',exact:true}).isDisabled(),true,'a dirty draft must block maintenance')
    await page.setViewportSize({width:320,height:900})
    await page.emulateMedia({colorScheme:'dark'})
    // Explicit host theme: OS preference alone need not switch a saved Koishi theme.
    await page.evaluate(()=>document.documentElement.classList.add('dark'))
    assert.notEqual(await page.locator('.taki-console').evaluate(e=>getComputedStyle(e).getPropertyValue('--bg').trim()),'#f4f3f7')
    assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=window.innerWidth),true,'320px horizontal overflow')
    await page.keyboard.press('Tab')
    assert.equal(await page.evaluate(()=>document.activeElement!==document.body),true)
    await page.screenshot({path:path.join(output,'groups-320-dark.png'),fullPage:true})
    if(await page.getByRole('dialog',{name:'群详情'}).count())await page.getByRole('button',{name:'关闭群详情'}).tap()
    await page.getByRole('button',{name:'打开导航'}).tap()
    const navigationDialog=page.getByRole('dialog',{name:'导航',exact:true});await navigationDialog.waitFor()
    assert.equal(await navigationDialog.evaluate(e=>e.contains(document.activeElement)),true,'opening navigation must move keyboard focus inside')
    await navigationDialog.getByRole('button',{name:'总览',exact:true}).focus();await page.keyboard.press('Shift+Tab')
    assert.equal(await navigationDialog.evaluate(e=>e.contains(document.activeElement)),true,'navigation must retain keyboard focus')
    await page.keyboard.press('Escape')
    assert.equal(await page.getByRole('button',{name:'打开导航'}).evaluate(e=>e===document.activeElement),true,'closing navigation must return focus')
    await page.getByRole('button',{name:'打开导航'}).tap();await nav.getByText('请求记录',{exact:true}).tap()
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
