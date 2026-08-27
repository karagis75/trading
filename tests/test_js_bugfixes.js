'use strict';

const assert = require('assert');
const path = require('path');

const minervini = require(path.join(__dirname, '..', 'fib_minervini_scanner.js'));
const sp500 = require(path.join(__dirname, '..', 'ussp500bull.js'));

function partitionWaveResults(allResults) {
    const wave1 = allResults.filter(r => String(r['Wave Position']).includes('Wave 1'));
    const wave3 = allResults.filter(r => {
        const p = String(r['Wave Position']);
        return p.includes('Wave 3') && !p.includes('Wave 1 of 3');
    });
    const wave5 = allResults.filter(r => {
        const p = String(r['Wave Position']);
        return p.includes('Wave 5') || p.includes('Super Extended');
    });
    return { wave1, wave3, wave5 };
}

function makeRisingRows(n, extras = {}) {
    const rows = [];
    for (let i = 0; i < n; i++) {
        const close = 100 + i * 0.4;
        rows.push({
            date: `2024${String((i % 12) + 1).padStart(2, '0')}01`,
            open: close - 0.2,
            high: close + 1.2,
            low: close - 1.0,
            close,
            volume: extras.volume || 1_000_000,
        });
    }
    return rows;
}

function testWaveBucketsDoNotOverlapWave1Of3() {
    const rows = [
        { 'Wave Position': 'Wave 1' },
        { 'Wave Position': 'Wave 1 of 3' },
        { 'Wave Position': 'Early Wave 1 of 3' },
        { 'Wave Position': 'Wave 3' },
        { 'Wave Position': 'Wave 3 Extended' },
        { 'Wave Position': 'Wave 5' },
        { 'Wave Position': 'Wave 1 of 3 (Bearish)' },
        { 'Wave Position': 'Wave 3 (Bearish)' },
    ];
    const { wave1, wave3, wave5 } = partitionWaveResults(rows);
    assert.strictEqual(wave1.length, 4, 'Wave 1 bucket should include Wave 1 and Wave 1 of 3 labels');
    assert.deepStrictEqual(wave3.map(r => r['Wave Position']).sort(), ['Wave 3', 'Wave 3 (Bearish)', 'Wave 3 Extended']);
    assert.strictEqual(wave5.length, 1);
}

function testMinerviniUsesHighFor52wAndIgnoresInvalidW2() {
    const rows = makeRisingRows(280);
    // Spike a higher high earlier in the year than the latest close, so close-based 52W high is too low.
    rows[200].high = 400;
    rows[200].close = 180;

    const passed = minervini.processCustomScanner('DEMO.JSON', rows);
    assert.ok(passed, 'steadily rising series should pass Minervini filters');
    assert.ok(passed['52W High Cap'] >= 400 * 1.25 - 0.01, '52W high cap should be based on high, not close');

    // Force price below the most recent W2 low: label must not become Wave 1 of 3 from a negative ratio.
    const last = rows[rows.length - 1];
    last.close = last.low - 5;
    last.high = last.close + 0.5;
    last.low = last.close - 0.5;
    const invalidated = minervini.processCustomScanner('DEMO.JSON', rows);
    if (invalidated) {
        assert.notStrictEqual(invalidated['Wave Target'], 'Wave 1 of 3 (Pinball)');
        assert.ok(invalidated['Ext Ratio'] >= 0, 'invalidated setups must not keep a negative ext ratio label');
    }
}

function testEarlyWave1RejectsEmptyPriorWindow() {
    const rows = [];
    for (let i = 0; i < 40; i++) {
        const close = 100 + i * 0.2;
        rows.push({
            date: `2024-01-${String(i + 1).padStart(2, '0')}`,
            open: close,
            high: close + 1,
            low: close - 1,
            close,
            volume: 1,
        });
    }
    rows[0].low = 99;
    const result = sp500.detectEarlyWave1('TEST', rows, rows, rows[rows.length - 1].close, rows[rows.length - 1].date);
    assert.strictEqual(result, null);
}

function testYahooClassShareEncoding() {
    const source = require('fs').readFileSync(path.join(__dirname, '..', 'ussp500bull.js'), 'utf8');
    assert.ok(source.includes("replace(/\\./g, '-')"), 'Yahoo symbols should map BRK.B to BRK-B');
    assert.ok(source.includes('encodeURIComponent(yahooSymbol)'), 'Yahoo chart path should be encoded');
}

function testNiftyPinballUsesW1Price() {
    const source = require('fs').readFileSync(path.join(__dirname, '..', 'nifty_pinball_yahoo.js'), 'utf8');
    assert.doesNotMatch(source, /w1c\.high/);
    assert.match(source, /curPrice <= w1c\.price/);
}

testWaveBucketsDoNotOverlapWave1Of3();
testMinerviniUsesHighFor52wAndIgnoresInvalidW2();
testEarlyWave1RejectsEmptyPriorWindow();
testYahooClassShareEncoding();
testNiftyPinballUsesW1Price();

console.log('All JavaScript bugfix tests passed.');
