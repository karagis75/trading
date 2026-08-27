'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');

const {
    analyzeFibPinball,
    isWaveBucket,
    toYYYYMMDDStr
} = require('./nifty_pinball_yahoo');

test('Yahoo timestamps are formatted in UTC', () => {
    const timestamp = new Date('2024-01-15T00:00:00.000Z');
    assert.equal(toYYYYMMDDStr(timestamp), '20240115');
});

test('wave 1 of 3 belongs only to the wave 1 report', () => {
    assert.equal(isWaveBucket('Wave 1 of 3', 1), true);
    assert.equal(isWaveBucket('Wave 1 of 3', 3), false);
    assert.equal(isWaveBucket('Super Extended', 5), true);
});

test('price between W2 and W1 is detected as early wave 1 of 3', () => {
    const rows = Array.from({ length: 80 }, (_, index) => ({
        date: `202601${String(index + 1).padStart(2, '0')}`,
        open: 107,
        high: 110,
        low: 105,
        close: 107,
        volume: 1_000
    }));

    rows[35] = { ...rows[35], open: 95, high: 100, low: 90, close: 95 };
    rows[45] = { ...rows[45], open: 110, high: 120, low: 108, close: 115 };
    rows[55] = { ...rows[55], open: 105, high: 110, low: 100, close: 105 };
    rows[79] = { ...rows[79], open: 108, high: 112, low: 107, close: 110 };

    const result = analyzeFibPinball('^NSEI', rows);
    assert.ok(result);
    assert.equal(result['Wave Position'], 'Early Wave 1 of 3');
});
