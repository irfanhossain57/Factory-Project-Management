const { chromium } = require('playwright');
(async()=>{
const browser=await chromium.launch({headless:true,args:['--no-sandbox']});
const page=await browser.newPage({viewport:{width:1365,height:1100}});
const errors=[];page.on('pageerror',e=>errors.push(e.message));
await page.goto(process.env.BASE_URL || 'http://127.0.0.1:8765');
await page.waitForFunction(()=>document.querySelector('#controller').textContent==='ONLINE');
await page.click('#recover');
await page.waitForFunction(()=>document.querySelector('#mode').textContent==='AUTOMATIC');
await page.selectOption('#vehicleType','TRUCK');
await page.fill('#vehicleId','UI-TRUCK');
await page.click('#vehicleForm button[type=submit]');
await page.waitForFunction(()=>document.querySelector('#total').textContent==='1');
await page.click('#duplicate');
await page.waitForTimeout(1200);
if(await page.locator('#total').textContent()!=='1')throw Error('Duplicate changed queue');
await page.waitForFunction(()=>document.querySelector('#signal-NORTH').textContent.includes('GREEN'),null,{timeout:6000});
await page.selectOption('#direction','EAST');
await page.selectOption('#vehicleType','EMERGENCY');
await page.fill('#vehicleId','UI-AMBULANCE');
await page.click('#vehicleForm button[type=submit]');
await page.waitForFunction(()=>document.querySelector('#mode').textContent==='EMERGENCY');

await page.waitForFunction(()=>document.querySelector('#signal-EAST').textContent.includes('GREEN'),null,{timeout:15000});
await page.locator('#vehicles tr').filter({hasText:'UI-AMBULANCE'}).getByRole('button',{name:'Clear'}).click();
await page.waitForFunction(()=>document.querySelector('#total').textContent==='1');
await page.setViewportSize({width:390,height:844});

const overflow=await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth);
if(overflow)throw Error('Mobile horizontal overflow');
if(errors.length)throw Error(errors.join('\n'));
console.log('PASS: browser startup/recovery, arrival, duplicate, emergency, clearance, mobile layout; no JavaScript errors');
await browser.close();
})().catch(e=>{console.error(e);process.exit(1)});
