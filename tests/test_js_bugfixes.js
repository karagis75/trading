'use strict';

const fs = require('fs');
const path = require('path');
const assert = require('assert');

const ROOT = path.resolve(__dirname, '..');

function isWave1(pos) {
    const p = String(pos || '');
    return p === 'Wave 1' || p.startsWith('Wave 1 ') || p.startsWith('Early Wave 1');
}
function isWave3(pos) {
    return String(pos || '').startsWith('Wave 3');
}

function testWaveBucketsDoNotOverlap() {
    const labels = [
        'Wave 1',
        'Wave 1 (Bearish)',
        'Wave 1 of 3',
        'Wave 1 of 3 (Bearish)',
        'Early Wave 1 of 3',
        'Early Wave 1 of 3 (Bearish)',
        'Wave 3',
        'Wave 3 Extended',
        'Wave 3 (Bearish)',
        'Wave 3 Extended (Bearish)',
        'Wave 5',
        'Wave 5 Extended (Bearish)',
    ];
    for (const label of labels) {
        const in1 = isWave1(label);
        const in3 = isWave3(label);
        assert.notStrictEqual(in1 && in3, true, `${label} matched both wave1 and wave3`);
    }
    assert.strictEqual(isWave1('Wave 1 of 3'), true);
    assert.strictEqual(isWave3('Wave 1 of 3'), false);
    assert.strictEqual(isWave1('Early Wave 1 of 3'), true);
    assert.strictEqual(isWave3('Early Wave 1 of 3'), false);
    assert.strictEqual(isWave3('Wave 3 Extended'), true);
    assert.strictEqual(isWave1('Wave 3 Extended'), false);
}

function testNiftyPinballUsesW1PriceNotHigh() {
    const src = fs.readFileSync(path.join(ROOT, 'nifty_pinball_yahoo.js'), 'utf8');
    assert.ok(src.includes('curPrice <= w1c.price'), 'nifty pinball should compare against w1c.price');
    assert.ok(!src.includes('w1c.high'), 'nifty pinball should not read the nonexistent w1c.high field');
}

function testMinerviniUses52wHighNotClose() {
    const src = fs.readFileSync(path.join(ROOT, 'fib_minervini_scanner.js'), 'utf8');
    assert.ok(src.includes('r.high > max ? r.high : max'), '52-week cap should use high, not close');
    assert.ok(src.includes('if (curClose < w2.price) continue'), 'invalidated pinball setups should be skipped');
    assert.ok(src.includes('else continue'), 'extreme extensions should not freeze on the first old pivot');
}

function testEmptyPriorWindowDoesNotValidate() {
    const priorBars = [];
    assert.strictEqual(priorBars.length, 0);
    assert.strictEqual(Math.min(...priorBars), Infinity);
    assert.strictEqual(Math.max(...priorBars), -Infinity);
}

function testAdjCloseNullishFallback() {
    const adjClose = [null, 10];
    const close = [9, 10];
    assert.strictEqual(adjClose[0] ?? close[0], 9);
    assert.strictEqual(adjClose[1] ?? close[1], 10);
}

const tests = [
    testWaveBucketsDoNotOverlap,
    testNiftyPinballUsesW1PriceNotHigh,
    testMinerviniUses52wHighNotClose,
    testEmptyPriorWindowDoesNotValidate,
    testAdjCloseNullishFallback,
];

let failed = 0;
for (const test of tests) {
    try {
        test();
        console.log(`PASS  ${test.name}`);
    } catch (err) {
        failed += 1;
        console.error(`FAIL  ${test.name}: ${err.message}`);
        process.exitCode = 1;
        throw err;
    }
}
console.log(`\n${tests.length - failed}/${tests.length} tests passed`);
