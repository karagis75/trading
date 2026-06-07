'use strict';

const fs = require('fs');
const path = require('path');

// — Configuration Directories —————————————————————————————————————————————
const CACHE_DIR = path.join(__dirname, 'data', 'yahoo_cache');
const OUT_DIR   = path.join(__dirname, 'results');

const PIVOT_LEFT  = parseInt(process.env.PIVOT_LEFT  || '5', 10);
const PIVOT_RIGHT = parseInt(process.env.PIVOT_RIGHT || '5', 10);

// — Technical Analysis Functions ———————————————————————————————————————————
const r2 = v => Math.round(v * 100) / 100;

function calculateEMA(rows, period, key = 'close') {
    const values = new Array(rows.length).fill(0);
    if (rows.length === 0) return values;
    
    const k = 2 / (period + 1);
    let currentEma = rows[0][key];
    values[0] = currentEma;

    for (let i = 1; i < rows.length; i++) {
        currentEma = rows[i][key] * k + currentEma * (1 - k);
        values[i] = currentEma;
    }
    return values;
}

function findPivots(rows, left = PIVOT_LEFT, right = PIVOT_RIGHT) {
    const raw = [];
    for (let i = left; i < rows.length - right; i++) {
        let isH = true, isL = true;
        for (let j = i - left; j <= i + right; j++) {
            if (j === i) continue;
            if (rows[j].high >= rows[i].high) isH = false;
            if (rows[j].low  <= rows[i].low)  isL = false;
        }
        if (isH) raw.push({ idx: i, type: 'H', price: rows[i].high, date: rows[i].date });
        if (isL) raw.push({ idx: i, type: 'L', price: rows[i].low,  date: rows[i].date });
    }
    raw.sort((a, b) => a.idx - b.idx);
    
    const alt = [];
    for (const p of raw) {
        if (alt.length === 0) { alt.push(p); continue; }
        const prev = alt[alt.length - 1];
        if (prev.type === p.type) {
            if (p.type === 'H' && p.price > prev.price) alt[alt.length - 1] = p;
            if (p.type === 'L' && p.price < prev.price) alt[alt.length - 1] = p;
        } else {
            alt.push(p);
        }
    }
    return alt;
}

// — Multi-Timeframe Web Scanner Matrix —————————————————————————————————————
function processCustomScanner(symbol, rows) {
    const n = rows.length;
    // We require an expanded dataset to reliably extract 52 weeks (approx 250 trading bars)
    if (n < 260) return null;

    const current = rows[n - 1];
    const curClose = current.close;
    const curVol   = current.volume;

    // 1. Calculate EMAs matching scanner parameters
    const ema50   = calculateEMA(rows, 50, 'close');
    const ema150  = calculateEMA(rows, 150, 'close');
    const ema200  = calculateEMA(rows, 200, 'close');
    const emaVol20 = calculateEMA(rows, 20, 'volume');

    const curEma50   = ema50[n - 1];
    const curEma150  = ema150[n - 1];
    const curEma200  = ema200[n - 1];
    const curEmaVol20 = emaVol20[n - 1];

    // Get EMA 200 from 1 month ago (approx 21 trading days ago)
    const prevMonthIdx = Math.max(0, n - 1 - 21);
    const historicalEma200 = ema200[prevMonthIdx];

    // 2. Extract 52-Week Structural Extremes (250 trading days)
    const yearSlice = rows.slice(-250);
    const weeklyMin52 = yearSlice.reduce((min, r) => r.low < min ? r.low : min, Infinity);
    const weeklyMax52 = yearSlice.reduce((max, r) => r.close > max ? r.close : max, -Infinity);

    // 3. Evaluate Script Parameters Exactly
    const cond1 = (curClose >= curEma150) && (curClose >= curEma200);
    const cond2 = (curEma150 >= curEma200);
    const cond3 = (curEma200 > historicalEma200);
    const cond4 = (curEma50 > curEma150) && (curEma50 > curEma200);
    const cond5 = (curClose > curEma50);
    const cond6 = (weeklyMin52 * 1.30) < curClose;
    const cond7 = curClose <= (weeklyMax52 * 1.25);
    const cond8 = curVol >= curEmaVol20;

    // Filter Out Tickers If Any Scanner Parameters Fail
    if (!(cond1 && cond2 && cond3 && cond4 && cond5 && cond6 && cond7 && cond8)) {
        return null;
    }

    // 4. Pinball Pattern Identification (Cross-Referencing the Verified Candidates)
    const pivots = findPivots(rows);
    let waveLabel = 'Unassigned Structural Setup';
    let extRatio = 0;

    for (let i = pivots.length - 1; i >= 2; i--) {
        const w2 = pivots[i], w1 = pivots[i-1], w0 = pivots[i-2];
        if (w2.type !== 'L' || w1.type !== 'H' || w0.type !== 'L') continue;

        const w1Amp = w1.price - w0.price;
        if (w1Amp <= 0 || w2.price <= w0.price) continue;

        const w2Retrace = (w1.price - w2.price) / w1Amp;
        if (w2Retrace < 0.236 || w2Retrace > 0.886) continue;

        extRatio = (curClose - w2.price) / w1Amp;
        if (extRatio <= 0.618) waveLabel = 'Wave 1 of 3 (Pinball)';
        else if (extRatio <= 1.236) waveLabel = 'Wave 3 Pinball Breakout';
        else if (extRatio <= 2.000) waveLabel = 'Wave 5 Extended Target';
        break;
    }

    return {
        Symbol:       symbol.replace('.JSON', ''),
        Price:        r2(curClose),
        Volume:       curVol,
        'Vol EMA20':  r2(curEmaVol20),
        'EMA50':      r2(curEma50),
        'EMA150':     r2(curEma150),
        'EMA200':     r2(curEma200),
        'EMA200 1M Ago': r2(historicalEma200),
        '52W Low Floor': r2(weeklyMin52 * 1.3),
        '52W High Cap':   r2(weeklyMax52 * 1.25),
        'Wave Target': waveLabel,
        'Ext Ratio':   r2(extRatio)
    };
}

// — Execution Logic ————————————————————————————————————————————————————————
function main() {
    if (!fs.existsSync(CACHE_DIR)) {
        console.error(`Error: Data directory not detected at ${CACHE_DIR}. Ensure historical data is generated first.`);
        process.exit(1);
    }

    console.log('================================================================================');
    console.log(' 🔎 RUNNING CUSTOM WEB SCREENER CONTEXT');
    console.log('================================================================================');
    console.log('Applying: EMA(50,150,200), Multi-timeframe 52W Extremes, Volume Multipliers...\n');

    const cacheFiles = fs.readdirSync(CACHE_DIR).filter(f => f.endsWith('.json'));
    const matches = [];

    for (const file of cacheFiles) {
        try {
            const filePath = path.join(CACHE_DIR, file);
            const rows = JSON.parse(fs.readFileSync(filePath, 'utf8'));
            const passed = processCustomScanner(file.toUpperCase(), rows);
            if (passed) {
                matches.push(passed);
            }
        } catch (err) {}
    }

    // Sort outputs descending based on volume intensity above its average standard baseline
    matches.sort((a, b) => (b.Volume / b['Vol EMA20']) - (a.Volume / a['Vol EMA20']));

    // Save Output Reports to CSV
    fs.mkdirSync(OUT_DIR, { recursive: true });
    const destinationPath = path.join(OUT_DIR, 'chartink_custom_matches.csv');
    
    if (matches.length > 0) {
        const headers = Object.keys(matches[0]);
        const csvRows = [
            headers.join(','),
            ...matches.map(row => headers.map(h => row[h]).join(','))
        ].join('\n');
        fs.writeFileSync(destinationPath, csvRows, 'utf8');
    }

    console.log(`Scan complete. Found ${matches.length} tickers perfectly matching all structural search properties.\n`);
    console.log('Top Screened Tickers matching Dashboard Profile:');
    console.log('--------------------------------------------------------------------------------');
    console.log('Ticker       | Close    | EMA 50   | EMA 150  | EMA 200  | Pinball Position');
    console.log('--------------------------------------------------------------------------------');
    
    matches.slice(0, 25).forEach(m => {
        console.log(
            `${m.Symbol.padEnd(12)} | ` +
            `${String(m.Price).padEnd(8)} | ` +
            `${String(m.EMA50).padEnd(8)} | ` +
            `${String(m.EMA150).padEnd(8)} | ` +
            `${String(m.EMA200).padEnd(8)} | ` +
            `${m['Wave Target']}`
        );
    });
    console.log('--------------------------------------------------------------------------------');
    console.log(`Full matching spreadsheet written to: ${destinationPath}`);
}

main();