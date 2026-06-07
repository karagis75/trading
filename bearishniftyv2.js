'use strict';

const fs    = require('fs');
const path  = require('path');
const https = require('https');

// — Configuration ——————————————————————————————————————————————————————————

// Hardcoded array for Nifty 50 and Bank Nifty Yahoo index symbols
const INDEX_SYMBOLS = ['^NSEI', '^NSEBANK'];

// Cache directory for Yahoo Finance JSON files
const CACHE_DIR = path.join(__dirname, 'data', 'yahoo_index_cache');
const OUT_DIR   = path.join(__dirname, 'results');

const LOOKBACK_DAYS        = parseInt(process.env.LOOKBACK_DAYS        || '500', 10);
const DELAY_MS             = parseInt(process.env.DELAY_MS             || '300', 10); 
const PIVOT_LEFT           = parseInt(process.env.PIVOT_LEFT           || '5',   10);
const PIVOT_RIGHT          = parseInt(process.env.PIVOT_RIGHT          || '5',   10);
const MAX_DAYS_SINCE_W2    = parseInt(process.env.MAX_DAYS_SINCE_W2    || '120', 10);
const MAX_DAYS_SINCE_W0    = parseInt(process.env.MAX_DAYS_SINCE_W0    || '180', 10);

// — General Utilities ——————————————————————————————————————————————————————

const sleep = ms => new Promise(r => setTimeout(r, ms));

function toYYYYMMDDStr(dateObj) {
    return (
        String(dateObj.getFullYear()) +
        String(dateObj.getMonth() + 1).padStart(2, '0') +
        String(dateObj.getDate()).padStart(2, '0')
    );
}

// — Direct HTTPS Getter for Yahoo Finance ——————————————————————————————————

function downloadYahooData(yahooSymbol, period1, period2) {
    // Caret index symbols require raw inclusion to avoid query parsing rejections
    const targetSymbol = yahooSymbol.startsWith('^') ? yahooSymbol : encodeURIComponent(yahooSymbol);
    const url = `https://query1.finance.yahoo.com/v8/finance/chart/${targetSymbol}?period1=${period1}&period2=${period2}&interval=1d&includeTimestamps=true`;

    return new Promise((resolve, reject) => {
        const options = {
            headers: {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36',
                'Accept': 'application/json',
                'Connection': 'keep-alive'
            }
        };

        https.get(url, options, (res) => {
            if (res.statusCode !== 200) {
                res.resume();
                return reject(new Error(`HTTP status ${res.statusCode} for url: ${url}`));
            }

            let rawData = '';
            res.on('data', chunk => rawData += chunk);
            res.on('end', () => {
                try {
                    resolve(JSON.parse(rawData));
                } catch (e) {
                    reject(e);
                }
            });
        }).on('error', reject);
    });
}

// — Fetching & Reformatting Pipeline ————————————————————————————————————————

async function fetchIndexHistoryFromYahoo(symbol) {
    const safeFilename = symbol.replace('^', 'INDEX_');
    const cacheFile = path.join(CACHE_DIR, `${safeFilename}.json`);
    
    if (fs.existsSync(cacheFile)) {
        try { 
            return JSON.parse(fs.readFileSync(cacheFile, 'utf8')); 
        } catch (e) {}
    }

    const endDate = new Date();
    const startDate = new Date();
    startDate.setDate(endDate.getDate() - LOOKBACK_DAYS);

    const period1 = Math.floor(startDate.getTime() / 1000);
    const period2 = Math.floor(endDate.getTime() / 1000);

    try {
        const json = await downloadYahooData(symbol, period1, period2);
        const chart = json.chart?.result?.[0];
        if (!chart || !chart.timestamp) return [];

        const timestamps = chart.timestamp;
        const indicators = chart.indicators.quote[0];
        const adjClose = chart.indicators.adjclose?.[0]?.adjclose || indicators.close;

        const parsedRows = [];
        for (let i = 0; i < timestamps.length; i++) {
            if (indicators.open?.[i] == null || indicators.close?.[i] == null || indicators.high?.[i] == null || indicators.low?.[i] == null) continue;

            const d = new Date(timestamps[i] * 1000);
            
            parsedRows.push({
                date:   toYYYYMMDDStr(d),
                symbol: symbol === '^NSEI' ? 'NIFTY50' : (symbol === '^NSEBANK' ? 'BANKNIFTY' : symbol),
                open:   indicators.open[i],
                high:   indicators.high[i],
                low:    indicators.low[i],
                close:  adjClose[i], 
                volume: indicators.volume[i] || 0
            });
        }

        if (parsedRows.length > 0) {
            fs.writeFileSync(cacheFile, JSON.stringify(parsedRows), 'utf8');
        }
        return parsedRows;

    } catch (err) {
        console.warn(`[YAHOO-ERR] Failed to download index details for ${symbol}: ${err.message}`);
        return [];
    }
}

// — CSV writer —————————————————————————————————————————————————————————————

function writeCsv(filePath, rows, headers) {
    const lines = [
        headers.join(','),
        ...rows.map(r => 
            headers.map(h => {
                const v = String(r[h] ?? '');
                return v.includes(',') ? `"${v}"` : v;
            }).join(',')
        )
    ];
    fs.writeFileSync(filePath, lines.join('\n') + '\n', 'utf8');
}

// — Elliott Wave / Bearish Fibonacci Pinball Analysis ——————————————————————

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

const r2 = v => Math.round(v * 100) / 100;

function analyzeBearishFibPinball(symbol, rows) {
    if (rows.length < 60) return null;

    const useRows  = rows.length > 200 ? rows.slice(-200) : rows.slice();
    const nUse     = useRows.length;
    const current  = rows[rows.length - 1];
    const curPrice = current.close;
    const curDate  = current.date;
    const cleanSymbol = symbol === '^NSEI' ? 'NIFTY50' : (symbol === '^NSEBANK' ? 'BANKNIFTY' : symbol);

    const pivots = findPivots(useRows);
    if (pivots.length < 3) return null;

    let best = null;

    for (let i = pivots.length - 1; i >= 2; i--) {
        const w2c = pivots[i];
        if (w2c.type !== 'H') continue; 

        const w1c = pivots[i - 1];
        if (w1c.type !== 'L') continue; 

        const w0c = pivots[i - 2];
        if (w0c.type !== 'H') continue; 

        const daysSinceW2 = nUse - 1 - w2c.idx;
        if (daysSinceW2 > MAX_DAYS_SINCE_W2) continue;

        const daysSinceW0 = nUse - 1 - w0c.idx;
        if (daysSinceW0 > MAX_DAYS_SINCE_W0) continue;

        const w1Amp = w0c.price - w1c.price;
        if (w1Amp <= 0) continue;

        if (w2c.price >= w0c.price) continue;

        const w2Retrace = (w2c.price - w1c.price) / w1Amp;
        if (w2Retrace < 0.236 || w2Retrace > 0.886) continue;

        const base = w1Amp; 
        const ext = ratio => w2c.price - ratio * base;
        const levels = {
            e0_382: ext(0.382), e0_618: ext(0.618), e0_764: ext(0.764),
            e1_000: ext(1.000), e1_236: ext(1.236), e1_382: ext(1.382),
            e1_618: ext(1.618), e1_764: ext(1.764), e2_000: ext(2.000)
        };

        let waveLabel       = null;
        let waveConfidence  = 0; 
        let waveDescription = '';

        if (curPrice > w2c.price) {
            // Setup Invalidated
        } else if (curPrice >= w1c.price) {
            if (daysSinceW2 <= 30 && curPrice < w2c.price) {
                waveLabel       = 'Early Wave 1 of 3 (Bearish)';
                waveConfidence  = 55;
                waveDescription = `Possible early downward w1 of Wave 3; index rejected from W2 High (${r2(w2c.price)}) but not yet broken below W1 Low (${r2(w1c.price)})`;
            }
        } else {
            const priceBelowW2 = w2c.price - curPrice;
            const extRatio     = priceBelowW2 / base; 

            if (extRatio <= 0.618) {
                waveLabel       = 'Wave 1 of 3 (Bearish)';
                waveConfidence  = 65;
                waveDescription = `In sub-wave 1 of Bearish Wave III; index (${r2(curPrice)}) broke below W1 low (${r2(w1c.price)}) at ${r2(extRatio * 100)}% of W1 amplitude from W2`;
            } else if (extRatio <= 1.236) {
                waveLabel       = 'Wave 3 (Bearish)';
                waveConfidence  = 80;
                waveDescription = `In Bearish Wave III (strong acceleration down); index (${r2(curPrice)}) at ${r2(extRatio * 100)}% extension. Targets: 1.0 ext=${r2(levels.e1_000)} to 1.618 ext=${r2(levels.e1_618)}`;
            } else if (extRatio <= 1.618) {
                const recentHigh20 = useRows.slice(-20).reduce((m, r) => r.high > m ? r.high : m, -Infinity);
                const w4Pullback  = (recentHigh20 < levels.e0_764) && (recentHigh20 > levels.e1_382);
                if (w4Pullback) {
                    waveLabel       = 'Wave 5 (Bearish)';
                    waveConfidence  = 72;
                    waveDescription = `In Bearish Wave V; W3 completed below 1.236 ext; corrective W4 bounced up to ~${r2(recentHigh20)}. Wave V targets: 1.764 ext=${r2(levels.e1_764)} to 2.0 ext=${r2(levels.e2_000)}`;
                } else {
                    waveLabel       = 'Wave 3 Extended (Bearish)';
                    waveConfidence  = 75;
                    waveDescription = `In extended Bearish Wave III; index (${r2(curPrice)}) at ${r2(extRatio * 100)}% ext; extended target 1.618 ext=${r2(levels.e1_618)}`;
                }
            } else if (extRatio <= 2.000) {
                const recentHigh20 = useRows.slice(-20).reduce((m, r) => r.high > m ? r.high : m, -Infinity);
                const w4Pullback  = recentHigh20 < levels.e1_000 && recentHigh20 > levels.e1_618;
                if (w4Pullback) {
                    waveLabel       = 'Wave 5 (Bearish)';
                    waveConfidence  = 78;
                    waveDescription = `In Bearish Wave V; W3 extended to ${r2(extRatio * 100)}% ext; W4 dead-cat bounce peaked near ~${r2(recentHigh20)}. Targets: ${r2(levels.e1_764)} to ${r2(levels.e2_000)}`;
                } else {
                    waveLabel       = 'Wave 5 Extended (Bearish)';
                    waveConfidence  = 65;
                    waveDescription = `In extended Bearish Wave V territory at ${r2(extRatio * 100)}% ext (${r2(curPrice)})`;
                }
            } else {
                waveLabel           = 'Super Extended (Bearish)';
                waveConfidence      = 50;
                waveDescription     = `Index expanded past 2.0 downside extension (${r2(extRatio * 100)}% of W1 drop); structural capitulation or higher-degree sell-off`;
            }
        }

        if (!waveLabel) continue;

        best = {
            Symbol:          cleanSymbol,
            'Last Date':     curDate,
            'Wave Position': waveLabel,
            Confidence:      waveConfidence,
            Description:     waveDescription,
            'W0 High':       r2(w0c.price),
            'W0 Date':       w0c.date,
            'W1 Low':        r2(w1c.price),
            'W1 Date':       w1c.date,
            'W2 High':       r2(w2c.price),
            'W2 Date':       w2c.date,
            'W2 Retrace %':  r2(w2Retrace * 100),
            'W1 Amplitude':  r2(base),
            'Current Price': r2(curPrice),
            'Ext Ratio':     r2((w2c.price - curPrice) / base),
            '0.382 Ext':     r2(levels.e0_382),
            '0.618 Ext':     r2(levels.e0_618),
            '0.764 Ext':     r2(levels.e0_764),
            '1.000 Ext':     r2(levels.e1_000),
            '1.236 Ext':     r2(levels.e1_236),
            '1.382 Ext':     r2(levels.e1_382),
            '1.618 Ext':     r2(levels.e1_618),
            '1.764 Ext':     r2(levels.e1_764),
            '2.000 Ext':     r2(levels.e2_000),
            'Days Since W2': daysSinceW2,
            'Days Since W0': daysSinceW0,
            'Days of Data':  rows.length
        };
        break; 
    }

    if (!best) {    
        best = detectEarlyBearishWave1(cleanSymbol, rows, useRows, curPrice, curDate);
    }
    return best;
}

function detectEarlyBearishWave1(cleanSymbol, rows, useRows, curPrice, curDate) {
    const n = useRows.length;
    if (n < 20) return null;

    let w0Idx = 0;
    let w0High = -Infinity;
    const lookStart = Math.max(0, n - MAX_DAYS_SINCE_W0);
    for (let i = lookStart; i < n; i++) {
        if (useRows[i].high > w0High) { w0High = useRows[i].high; w0Idx = i; }
    }

    const daysSinceW0 = n - 1 - w0Idx;
    if (daysSinceW0 < 5) return null;       
    if (daysSinceW0 > MAX_DAYS_SINCE_W0) return null;

    const lossFromW0 = (w0High - curPrice) / w0High;
    if (lossFromW0 < 0.01 || lossFromW0 > 0.40) return null; 

    const preW0Start = Math.max(0, w0Idx - 20);
    const priorHigh   = Math.max(...useRows.slice(preW0Start, w0Idx).map(r => r.high));
    if (w0High <= priorHigh) return null; 
    
    const slice10 = useRows.slice(-10);
    const sma10   = slice10.reduce((s, r) => s + r.close, 0) / slice10.length;
    if (curPrice > sma10) return null;

    const lowAfterW0 = Math.min(...useRows.slice(w0Idx).map(r => r.low));
    const w1Amplitude = w0High - lowAfterW0;
    if (w1Amplitude <= 0) return null;

    const w0Date = useRows[w0Idx].date;

    return {
        Symbol:          cleanSymbol,
        'Last Date':     curDate,
        'Wave Position': 'Wave 1 (Bearish)',
        Confidence:      60,
        Description:     `Early Bearish Wave 1: fresh local peak high at ${r2(w0High)} (${daysSinceW0} bars ago); index dropped ${r2(lossFromW0 * 100)}% from W0; low point reached so far: ${r2(lowAfterW0)}`,
        'W0 High':       r2(w0High),
        'W0 Date':       w0Date,
        'W1 Low':        r2(lowAfterW0),
        'W1 Date':       curDate,
        'W2 High':       '',
        'W2 Date':       '',
        'W2 Retrace %':  '',
        'W1 Amplitude':  r2(w1Amplitude),
        'Current Price': r2(curPrice),
        'Ext Ratio':     r2(lossFromW0),
        '0.382 Ext':     r2(w0High - 0.382 * w1Amplitude),
        '0.618 Ext':     r2(w0High - 0.618 * w1Amplitude),
        '0.764 Ext':     r2(w0High - 0.764 * w1Amplitude),
        '1.000 Ext':     r2(w0High - 1.000 * w1Amplitude),
        '1.236 Ext':     r2(w0High - 1.236 * w1Amplitude),
        '1.382 Ext':     r2(w0High - 1.382 * w1Amplitude),
        '1.618 Ext':     r2(w0High - 1.618 * w1Amplitude),
        '1.764 Ext':     r2(w0High - 1.764 * w1Amplitude),
        '2.000 Ext':     r2(w0High - 2.000 * w1Amplitude),
        'Days Since W2': '',
        'Days Since W0': daysSinceW0,
        'Days of Data':  rows.length
    };
}

// — Main Execution —————————————————————————————————————————————————————————

async function main() {
    fs.mkdirSync(CACHE_DIR, { recursive: true });
    fs.mkdirSync(OUT_DIR,   { recursive: true });

    console.log(`Targeting Indices (Downside Analysis): ${INDEX_SYMBOLS.join(', ')}`);
    console.log(`Lookback Configuration: ${LOOKBACK_DAYS} days from today.`);
    
    INDEX_SYMBOLS.forEach(s => {
        const p = path.join(CACHE_DIR, `${s.replace('^', 'INDEX_')}.json`);
        if (fs.existsSync(p)) fs.unlinkSync(p);
    });

    console.log('\nDownloading / loading historical data from Yahoo Finance...\n');

    const allResults = [];
    let processedCount = 0;

    for (const symbol of INDEX_SYMBOLS) {
        processedCount++;
        console.log(`[${processedCount}/${INDEX_SYMBOLS.length}] Syncing & Analyzing ${symbol}...`);
        
        const rows = await fetchIndexHistoryFromYahoo(symbol);

        if (rows.length < 60) {
            console.warn(`⚠️ Insufficient historical series retrieved for ${symbol} (${rows.length} records). Skipping.`);
            continue;
        }

        const result = analyzeBearishFibPinball(symbol, rows);
        if (result) {
            allResults.push(result);
        }
        await sleep(DELAY_MS);
    }
    
    console.log('\nData pipeline breakdown parsing complete.');

    const wave1 = allResults.filter(r => r['Wave Position'].includes('Wave 1'));
    const wave3 = allResults.filter(r => r['Wave Position'].includes('Wave 3'));
    const wave5 = allResults.filter(r => r['Wave Position'].includes('Wave 5') || r['Wave Position'].includes('Super Extended'));

    allResults.sort((a, b) => {
        const order = { 
            'Wave 3 (Bearish)': 0, 'Wave 3 Extended (Bearish)': 1, 'Wave 5 (Bearish)': 2, 'Wave 5 Extended (Bearish)': 3,
            'Wave 1 of 3 (Bearish)': 4, 'Wave 1 (Bearish)': 5, 'Early Wave 1 of 3 (Bearish)': 6, 'Super Extended (Bearish)': 7 
        };
        const oa = order[a['Wave Position']] ?? 99;
        const ob = order[b['Wave Position']] ?? 99;
        if (oa !== ob) return oa - ob;
        return b.Confidence - a.Confidence;
    });

    const HEADERS = [
        'Symbol', 'Last Date', 'Wave Position', 'Confidence', 'Description',
        'W0 High', 'W0 Date', 'W1 Low', 'W1 Date', 'W2 High', 'W2 Date',
        'W2 Retrace %', 'W1 Amplitude', 'Current Price', 'Ext Ratio',
        '0.382 Ext', '0.618 Ext', '0.764 Ext', '1.000 Ext', '1.236 Ext', '1.382 Ext',
        '1.618 Ext', '1.764 Ext', '2.000 Ext',
        'Days Since W2', 'Days Since W0', 'Days of Data'
    ];

    const outAll    = path.join(OUT_DIR, 'bearish_nifty_all.csv');
    const outWave1  = path.join(OUT_DIR, 'bearish_nifty_wave1.csv');
    const outWave3  = path.join(OUT_DIR, 'bearish_nifty_wave3.csv');
    const outWave5  = path.join(OUT_DIR, 'bearish_nifty_wave5.csv');

    writeCsv(outAll,    allResults, HEADERS);
    writeCsv(outWave1,  wave1,      HEADERS);
    writeCsv(outWave3,  wave3,      HEADERS);
    writeCsv(outWave5,  wave5,      HEADERS);

    console.log('\n================================================================================');
    console.log(' BEARISH FIBONACCI PINBALL INDEX RESULTS (YAHOO FINANCE)');
    console.log('================================================================================');
    if (allResults.length === 0) {
        console.log('No structures matched the parameters for standard bearish setups.');
    } else {
        allResults.forEach(r => {
            console.log(
                `${r.Symbol.padEnd(12)} | Position: ${r['Wave Position'].padEnd(22)} | ` +
                `Price: ${String(r['Current Price']).padStart(9)} | Conf: ${r.Confidence}%`
            );
            console.log(`Description: ${r.Description}\n---`);
        });
    }
    console.log('================================================================================');
    console.log(`\nGenerated reports stored inside directory: ${OUT_DIR}`);
}

main().catch(err => { console.error('Fatal Script Failure:', err); process.exit(1); });