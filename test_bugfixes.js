'use strict';

const fs = require('fs');
const path = require('path');
const https = require('https');
const assert = require('assert');
const vm = require('vm');

let passed = 0;
let failed = 0;

function check(name, cond, detail = '') {
  if (cond) {
    passed += 1;
    console.log(`PASS  ${name}`);
  } else {
    failed += 1;
    console.log(`FAIL  ${name}${detail ? ' — ' + detail : ''}`);
  }
}

function read(file) {
  return fs.readFileSync(path.join(__dirname, file), 'utf8');
}

function testCsvDefaultPaths() {
  for (const [file, expected] of [
    ['fib_yahoo_pinball.js', "path.join(__dirname, 'nifty500.csv')"],
    ['bearsish_fib_pin_ball.js', "path.join(__dirname, 'nifty500.csv')"],
    ['ussp500bull.js', "path.join(__dirname, 'sp500.csv')"],
  ]) {
    const src = read(file);
    check(`${file} default CSV at repo root`, src.includes(expected));
    check(`${file} does not default to data/`, !src.includes(`path.join(__dirname, 'data', '${expected.includes('sp500') ? 'sp500' : 'nifty500'}.csv')`));
  }
  check('nifty500.csv exists', fs.existsSync(path.join(__dirname, 'nifty500.csv')));
  check('sp500.csv exists', fs.existsSync(path.join(__dirname, 'sp500.csv')));
}

function testNiftyPinballW1Price() {
  const src = read('nifty_pinball_yahoo.js');
  check('nifty_pinball uses w1c.price', src.includes('curPrice <= w1c.price'));
  check('nifty_pinball does not use w1c.high', !src.includes('w1c.high'));
}

function testRawCloseConsistency() {
  for (const file of [
    'fib_yahoo_pinball.js',
    'bearsish_fib_pin_ball.js',
    'nifty_pinball_yahoo.js',
    'bearishniftyv2.js',
    'ussp500bull.js',
  ]) {
    const src = read(file);
    check(`${file} uses raw close`, src.includes('close:  indicators.close[i]') || src.includes('close: indicators.close[i]'));
    check(`${file} does not mix adjClose into close`, !/close:\s*adjClose\[i\]/.test(src));
  }
}

function testEarlyWavePriorGuard() {
  for (const file of [
    'fib_yahoo_pinball.js',
    'nifty_pinball_yahoo.js',
    'bearsish_fib_pin_ball.js',
    'bearishniftyv2.js',
  ]) {
    const src = read(file);
    check(`${file} guards empty prior slice`, src.includes('if (!priorSlice.length) return null;'));
  }
}

function testUsShareClassMapping() {
  const src = read('ussp500bull.js');
  check('US Yahoo maps . to -', src.includes(".replace(/\\./g, '-')"));
}

function httpsGet(url) {
  return new Promise((resolve, reject) => {
    https.get(url, { headers: { 'User-Agent': 'Mozilla/5.0' } }, (res) => {
      let raw = '';
      res.on('data', (c) => (raw += c));
      res.on('end', () => resolve({ status: res.statusCode, body: raw }));
    }).on('error', reject);
  });
}

async function testLiveYahooUsTickers() {
  const brkDot = await httpsGet('https://query1.finance.yahoo.com/v8/finance/chart/BRK.B?range=5d&interval=1d');
  const brkHyphen = await httpsGet('https://query1.finance.yahoo.com/v8/finance/chart/BRK-B?range=5d&interval=1d');
  check('BRK.B returns 404', brkDot.status === 404);
  check('BRK-B returns 200', brkHyphen.status === 200);
}

async function testSymbolFileLoad() {
  // Load just the readSymbols logic by requiring path existence used by scanners.
  const nifty = fs.readFileSync(path.join(__dirname, 'nifty500.csv'), 'utf8')
    .split(/\r?\n/).map((l) => l.trim()).filter(Boolean);
  const spx = fs.readFileSync(path.join(__dirname, 'sp500.csv'), 'utf8')
    .split(/\r?\n/).map((l) => l.trim()).filter(Boolean);
  check('nifty500 has symbols', nifty.length > 100);
  check('sp500 has symbols', spx.length > 100);
  check('sp500 includes BRK.B', spx.some((l) => l.startsWith('BRK.B')));
}

async function main() {
  testCsvDefaultPaths();
  testNiftyPinballW1Price();
  testRawCloseConsistency();
  testEarlyWavePriorGuard();
  testUsShareClassMapping();
  await testSymbolFileLoad();
  await testLiveYahooUsTickers();
  console.log(`\n${passed} passed, ${failed} failed`);
  process.exitCode = failed ? 1 : 0;
}

main().catch((err) => {
  console.error(err);
  process.exitCode = 1;
});
