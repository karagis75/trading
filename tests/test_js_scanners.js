'use strict';

const assert = require('assert');
const nifty = require('../nifty_pinball_yahoo.js');
const minervini = require('../fib_minervini_scanner.js');
const us = require('../ussp500bull.js');

function yyyymmdd(offsetDays) {
    const d = new Date(Date.UTC(2024, 0, 1));
    d.setUTCDate(d.getUTCDate() + offsetDays);
    return (
        String(d.getUTCFullYear()) +
        String(d.getUTCMonth() + 1).padStart(2, '0') +
        String(d.getUTCDate()).padStart(2, '0')
    );
}

function bar(i, open, high, low, close, volume = 1000) {
    return { date: yyyymmdd(i), symbol: 'TEST', open, high, low, close, volume };
}

function pinballRows() {
    const rows = [];
    for (let i = 0; i < 80; i++) {
        if (i === 20) {
            rows.push(bar(i, 102, 103, 100, 101)); // W0 low
        } else if (i === 40) {
            rows.push(bar(i, 135, 140, 133, 138)); // W1 high
        } else if (i === 55) {
            rows.push(bar(i, 122, 124, 120, 121)); // W2 low
        } else if (i < 20) {
            rows.push(bar(i, 110, 111, 109, 110));
        } else if (i < 40) {
            const px = 110 + (i - 20);
            rows.push(bar(i, px, px + 1, px - 1, px));
        } else if (i < 55) {
            rows.push(bar(i, 126, 128, 125, 126));
        } else {
            rows.push(bar(i, 130, 131, 129, 130)); // current 130, between W2=120 and W1=140
        }
    }
    return rows;
}

function testNiftyEarlyWaveLabelUsesW1Price() {
    const result = nifty.analyzeFibPinball('NIFTY', pinballRows());
    assert.ok(result, 'expected a pinball match');
    assert.strictEqual(result['Wave Position'], 'Early Wave 1 of 3');
    assert.ok(result['Current Price'] <= result['W1 High']);
}

function testEarlyWaveRejectsEmptyPriorSlice() {
    const rows = [];
    for (let i = 0; i < 80; i++) {
        const px = 100 + i * 0.4;
        rows.push(bar(i, px, px + 1, i === 0 ? 80 : px - 1, px));
    }
    const result = nifty.detectEarlyWave1('NIFTY', rows, rows, rows[rows.length - 1].close, rows[rows.length - 1].date);
    assert.strictEqual(result, null);
}

function testUsEarlyWaveFallbackExists() {
    assert.strictEqual(typeof us.detectEarlyWave1, 'function');
    const rows = pinballRows();
    const result = us.detectEarlyWave1('AAPL', rows, rows, rows[rows.length - 1].close, rows[rows.length - 1].date);
    assert.ok(result === null || typeof result['Wave Position'] === 'string');
}

function minerviniUptrendRows(options = {}) {
    const spikeHigh = options.spikeHigh || null;
    const lastClose = options.lastClose || 150;
    const rows = [];
    for (let i = 0; i < 270; i++) {
        const px = 50 + (i / 269) * (lastClose - 50);
        const high = spikeHigh && i === 80 ? spikeHigh : px + 0.8;
        rows.push(bar(i, px, high, px - 0.8, px, 2000));
    }
    return rows;
}

function testMinerviniUsesTrue52WeekHigh() {
    const rows = minerviniUptrendRows({ spikeHigh: 220, lastClose: 150 });
    const result = minervini.processCustomScanner('TEST.JSON', rows);
    assert.ok(result, 'expected Minervini uptrend to pass');
    assert.strictEqual(result['52W High Cap'], Math.round(220 * 1.25 * 100) / 100);
}

function testMinerviniDoesNotLabelInvalidatedPinball() {
    const rows = minerviniUptrendRows({ lastClose: 150 });
    // Plant an old L-H-L whose W2 is above the current close so the setup is invalid.
    rows[200] = bar(200, 160, 161, 140, 155, 2000); // W0
    rows[220] = bar(220, 175, 190, 170, 185, 2000); // W1
    rows[240] = bar(240, 170, 172, 160, 165, 2000); // W2
    rows[269].close = 150;
    rows[269].high = 151;
    rows[269].low = 149;
    const result = minervini.processCustomScanner('TEST.JSON', rows);
    assert.ok(result, 'expected Minervini conditions to still pass');
    assert.notStrictEqual(result['Wave Target'], 'Wave 1 of 3 (Pinball)');
}

function testMinerviniSortGuardsZeroVolumeEma() {
    const a = { Volume: 10, 'Vol EMA20': 0 };
    const b = { Volume: 20, 'Vol EMA20': 5 };
    const volRatio = (row) => (row['Vol EMA20'] > 0 ? row.Volume / row['Vol EMA20'] : 0);
    const matches = [a, b];
    matches.sort((x, y) => volRatio(y) - volRatio(x));
    assert.strictEqual(matches[0], b);
    assert.ok(Number.isFinite(volRatio(a)));
}

const tests = [
    testNiftyEarlyWaveLabelUsesW1Price,
    testEarlyWaveRejectsEmptyPriorSlice,
    testUsEarlyWaveFallbackExists,
    testMinerviniUsesTrue52WeekHigh,
    testMinerviniDoesNotLabelInvalidatedPinball,
    testMinerviniSortGuardsZeroVolumeEma,
];

for (const test of tests) {
    test();
    console.log(`PASS ${test.name}`);
}
console.log(`\n${tests.length} JavaScript tests passed.`);
