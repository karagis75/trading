
'use strict';

const fs    = require('fs');
const path  = require('path');
const https = require('https');

// — Configuration ——————————————————————————————————————————————————————————

const SYMBOLS_FILE = process.env.SYMBOLS_FILE 
    ? path.resolve(__dirname, process.env.SYMBOLS_FILE)
    : path.join(__dirname, 'data', 'nifty500.csv');

// Cache directory for Yahoo Finance JSON files
const CACHE_DIR = path.join(__dirname, 'data', 'yahoo_cache');
const OUT_DIR   = path.join(__dirname, 'results');

const LOOKBACK_DAYS        = parseInt(process.env.LOOKBACK_DAYS        || '420', 10);
const DELAY_MS             = parseInt(process.env.DELAY_MS             || '150', 10); // Lower delay since Yahoo is more stable than NSE
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

function downloadYahooData(symbol, period1, period2) {
    // Yahoo Finance requires the standard .NS suffix for National Stock Exchange of India tickers
    const yahooSymbol = symbol.endsWith('.NS') ? symbol : `${symbol}.NS`;
    const url = `https://query1.finance.yahoo.com/v8/finance/chart/${yahooSymbol}?period1=${period1}&period2=${period2}&interval=1d&includeTimestamps=true`;

    return new Promise((resolve, reject) => {
        const options = {
            headers: {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
            }
        };

        https.get(url, options, (res) => {
            if (res.statusCode !== 200) {
                res.resume();
                return reject(new Error(`HTTP status ${res.statusCode}`));
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

async function fetchSymbolHistoryFromYahoo(symbol) {
    const cacheFile = path.join(CACHE_DIR, `${symbol}.json`);
    
    // Check if cache file exists
    if (fs.existsSync(cacheFile)) {
        try { 
            return JSON.parse(fs.readFileSync(cacheFile, 'utf8')); 
        } catch (e) {}
    }

    // Calculate timestamps (Unix Epoch seconds)
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
            // Filter out any days missing critical price points
            if (indicators.open[i] == null || indicators.close[i] == null) continue;

            const d = new Date(timestamps[i] * 1000);
            
            // Reconstruct row objects to perfectly match the internal pipeline architecture
            parsedRows.push({
                date:   toYYYYMMDDStr(d),
                symbol: symbol.toUpperCase().replace('.NS', ''),
                open:   indicators.open[i],
                high:   indicators.high[i],
                low:    indicators.low[i],
                close:  adjClose[i], // Uses corporate action adjusted closes for accurate Fib retracements
                volume: indicators.volume[i] || 0
            });
        }

        if (parsedRows.length > 0) {
            fs.writeFileSync(cacheFile, JSON.stringify(parsedRows), 'utf8');
        }
        return parsedRows;

    } catch (err) {
        console.warn(`[YAHOO-ERR] Failed to download ${symbol}: ${err.message}`);
        return [];
    }
}

// — Symbol loading ——————————————————————————————————————————————————————————

function readSymbols(filePath) {
    if (!fs.existsSync(filePath)) { console.error('Symbols file not found:', filePath); process.exit(1); }
    const SKIP  = new Set(['SYMBOL', 'TICKER', 'SCRIP', 'CODE', 'NAME']);
    const lines = fs.readFileSync(filePath, 'utf8').split(/\r?\n/).map(l => l.trim()).filter(Boolean);
    if (!lines.length) return [];
    const isCsv = lines[0].includes(',');
    if (isCsv) {
        const hdrs = lines[0].split(',').map(h => h.trim().toLowerCase());
        const idx  = hdrs.findIndex(h => /symbol|ticker/.test(h));
        return [...new Set(
            lines.slice(1)
                .map(l => (l.split(',')[idx >= 0 ? idx : 0] || '').trim()
                    .toUpperCase().replace(/\.NS$/i, '').replace(/[^A-Z0-9\-&]/g, ''))
                .filter(s => s && !SKIP.has(s))
        )];
    }
    return [...new Set(
        lines
            .map(l => l.split(/[\s,]+/)[0].toUpperCase()
                .replace(/\.NS$/i, '').replace(/[^A-Z0-9\-&]/g, ''))
            .filter(s => s && !SKIP.has(s))
    )];
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

// — Elliott Wave / Fibonacci Pinball Analysis ——————————————————————————————

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

function analyzeFibPinball(symbol, rows) {
    if (rows.length < 60) return null;

    const useRows  = rows.length > 200 ? rows.slice(-200) : rows.slice();
    const nUse     = useRows.length;
    const current  = rows[rows.length - 1];
    const curPrice = current.close;
    const curDate  = current.date;

    const pivots = findPivots(useRows);
    if (pivots.length < 3) return null;

    let best = null;

    for (let i = pivots.length - 1; i >= 2; i--) {
        const w2c = pivots[i];
        if (w2c.type !== 'L') continue;

        const w1c = pivots[i - 1];
        if (w1c.type !== 'H') continue;

        const w0c = pivots[i - 2];
        if (w0c.type !== 'L') continue;

        const daysSinceW2 = nUse - 1 - w2c.idx;
        if (daysSinceW2 > MAX_DAYS_SINCE_W2) continue;

        const daysSinceW0 = nUse - 1 - w0c.idx;
        if (daysSinceW0 > MAX_DAYS_SINCE_W0) continue;

        const w1Amp = w1c.price - w0c.price;
        if (w1Amp <= 0) continue;

        if (w2c.price <= w0c.price) continue;

        const w2Retrace = (w1c.price - w2c.price) / w1Amp;
        if (w2Retrace < 0.236 || w2Retrace > 0.886) continue;

        const base = w1Amp; 
        const ext = ratio => w2c.price + ratio * base;
        const levels = {
            e0_382: ext(0.382), e0_618: ext(0.618), e0_764: ext(0.764),
            e1_000: ext(1.000), e1_236: ext(1.236), e1_382: ext(1.382),
            e1_618: ext(1.618), e1_764: ext(1.764), e2_000: ext(2.000)
        };

        let waveLabel       = null;
        let waveConfidence  = 0; 
        let waveDescription = '';

        if (curPrice < w2c.price) {
            // Slid below setup validation
        } else if (curPrice <= w1c.price) {
            if (daysSinceW2 <= 30 && curPrice > w2c.price) {
                waveLabel       = 'Early Wave 1 of 3';
                waveConfidence  = 55;
                waveDescription = `Possible early w1 of Wave 3; price bounced from W2 (${r2(w2c.price)}) but not yet above W1 high (${r2(w1c.price)})`;
            }
        } else {
            const priceAboveW2 = curPrice - w2c.price;
            const extRatio     = priceAboveW2 / base; 

            if (extRatio <= 0.618) {
                waveLabel       = 'Wave 1 of 3';
                waveConfidence  = 65;
                waveDescription = `In sub-wave 1 of Wave III; price (${r2(curPrice)}) broke above W1 high (${r2(w1c.price)}) at ${r2(extRatio * 100)}% of W1 amplitude from W2 (target range: 0.382-0.618 ext)`;
            } else if (extRatio <= 1.236) {
                waveLabel       = 'Wave 3';
                waveConfidence  = 80;
                waveDescription = `In Wave III (strongest wave); price (${r2(curPrice)}) at ${r2(extRatio * 100)}% Wave III targets: 1.0 ext=${r2(levels.e1_000)} to 1.618 ext=${r2(levels.e1_618)}`;
            } else if (extRatio <= 1.618) {
                const recentLow20 = useRows.slice(-20).reduce((m, r) => r.low < m ? r.low : m, Infinity);
                const w4Pullback  = (recentLow20 > levels.e0_764) && (recentLow20 < levels.e1_382);
                if (w4Pullback) {
                    waveLabel       = 'Wave 5';
                    waveConfidence  = 72;
                    waveDescription = `In Wave V; W3 completed above 1.236 ext; W4 pulled back to ~${r2(recentLow20)} Wave V targets: 1.764 ext=${r2(levels.e1_764)} to 2.0 ext=${r2(levels.e2_000)}`;
                } else {
                    waveLabel       = 'Wave 3 Extended';
                    waveConfidence  = 75;
                    waveDescription = `In extended Wave III; price (${r2(curPrice)}) at ${r2(extRatio * 100)}% ext; extended target 1.618 ext=${r2(levels.e1_618)}`;
                }
            } else if (extRatio <= 2.000) {
                const recentLow20 = useRows.slice(-20).reduce((m, r) => r.low < m ? r.low : m, Infinity);
                const w4Pullback  = recentLow20 > levels.e1_000 && recentLow20 < levels.e1_618;
                if (w4Pullback) {
                    waveLabel       = 'Wave 5';
                    waveConfidence  = 78;
                    waveDescription = `In Wave V; W3 extended to ${r2(extRatio * 100)}% ext; W4 low ~${r2(recentLow20)} Wave V targets: ${r2(levels.e1_764)} to ${r2(levels.e2_000)}`;
                } else {
                    waveLabel       = 'Wave 5 Extended';
                    waveConfidence  = 65;
                    waveDescription = `In extended Wave V territory at ${r2(extRatio * 100)}% ext (${r2(curPrice)}); extreme target 2.618 ext`;
                }
            } else {
                waveLabel           = 'Super Extended';
                waveConfidence      = 50;
                waveDescription     = `Price beyond 2.0 ext (${r2(extRatio * 100)}% of W1 amplitude from W2); potential blow-off or start of new higher-degree wave`;
            }
        }

        if (!waveLabel) continue;

        best = {
            Symbol:          symbol,
            'Last Date':     curDate,
            'Wave Position': waveLabel,
            Confidence:      waveConfidence,
            Description:     waveDescription,
            'W0 Low':        r2(w0c.price),
            'W0 Date':       w0c.date,
            'W1 High':       r2(w1c.price),
            'W1 Date':       w1c.date,
            'W2 Low':        r2(w2c.price),
            'W2 Date':       w2c.date,
            'W2 Retrace %':  r2(w2Retrace * 100),
            'W1 Amplitude':  r2(base),
            'Current Price': r2(curPrice),
            'Ext Ratio':     r2((curPrice - w2c.price) / base),
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
        best = detectEarlyWave1(symbol, rows, useRows, curPrice, curDate);
    }
    return best;
}

function detectEarlyWave1(symbol, rows, useRows, curPrice, curDate) {
    const n = useRows.length;
    if (n < 20) return null;

    let w0Idx = 0;
    let w0Low = Infinity;
    const lookStart = Math.max(0, n - MAX_DAYS_SINCE_W0);
    for (let i = lookStart; i < n; i++) {
        if (useRows[i].low < w0Low) { w0Low = useRows[i].low; w0Idx = i; }
    }

    const daysSinceW0 = n - 1 - w0Idx;
    if (daysSinceW0 < 5) return null;       
    if (daysSinceW0 > MAX_DAYS_SINCE_W0) return null;

    const gainFromW0 = (curPrice - w0Low) / w0Low;
    if (gainFromW0 < 0.05 || gainFromW0 > 1.0) return null;

    const preW0Start = Math.max(0, w0Idx - 20);
    const priorLow   = Math.min(...useRows.slice(preW0Start, w0Idx).map(r => r.low));
    if (w0Low >= priorLow) return null; 
    
    const slice10 = useRows.slice(-10);
    const sma10   = slice10.reduce((s, r) => s + r.close, 0) / slice10.length;
    if (curPrice < sma10) return null;

    const highAfterW0 = Math.max(...useRows.slice(w0Idx).map(r => r.high));
    const w1Amplitude = highAfterW0 - w0Low;
    if (w1Amplitude <= 0) return null;

    const w0Date = useRows[w0Idx].date;

    return {
        Symbol:          symbol,
        'Last Date':     curDate,
        'Wave Position': 'Wave 1',
        Confidence:      60,
        Description:     `Early Wave 1: fresh low at ${r2(w0Low)} (${daysSinceW0} bars ago); price gained ${r2(gainFromW0 * 100)}% from W0; Wave 1 high so far: ${r2(highAfterW0)}`,
        'W0 Low':        r2(w0Low),
        'W0 Date':       w0Date,
        'W1 High':       r2(highAfterW0),
        'W1 Date':       curDate,
        'W2 Low':        '',
        'W2 Date':       '',
        'W2 Retrace %':  '',
        'W1 Amplitude':  r2(w1Amplitude),
        'Current Price': r2(curPrice),
        'Ext Ratio':     r2(gainFromW0),
        '0.382 Ext':     r2(w0Low + 0.382 * w1Amplitude),
        '0.618 Ext':     r2(w0Low + 0.618 * w1Amplitude),
        '0.764 Ext':     r2(w0Low + 0.764 * w1Amplitude),
        '1.000 Ext':     r2(w0Low + 1.000 * w1Amplitude),
        '1.236 Ext':     r2(w0Low + 1.236 * w1Amplitude),
        '1.382 Ext':     r2(w0Low + 1.382 * w1Amplitude),
        '1.618 Ext':     r2(w0Low + 1.618 * w1Amplitude),
        '1.764 Ext':     r2(w0Low + 1.764 * w1Amplitude),
        '2.000 Ext':     r2(w0Low + 2.000 * w1Amplitude),
        'Days Since W2': '',
        'Days Since W0': daysSinceW0,
        'Days of Data':  rows.length
    };
}

// — Main Execution —————————————————————————————————————————————————————————

async function main() {
    fs.mkdirSync(CACHE_DIR, { recursive: true });
    fs.mkdirSync(OUT_DIR,   { recursive: true });

    // 1. Load symbols
    const symbolsList = readSymbols(SYMBOLS_FILE);
    console.log(`Loaded ${symbolsList.length} symbols from ${path.basename(SYMBOLS_FILE)}`);
    console.log(`Lookback Configuration: ${LOOKBACK_DAYS} days from today.`);
    console.log('\nDownloading / loading historical data from Yahoo Finance...\n');

    const allResults = [];
    const insufficient = [];
    let processedCount = 0;

    // 2. Fetch history on a per-ticker model structure
    for (const symbol of symbolsList) {
        processedCount++;
        const isCached = fs.existsSync(path.join(CACHE_DIR, `${symbol}.json`));
        
        process.stdout.write(`[${processedCount}/${symbolsList.length}] Processing ${symbol.padEnd(12)} (${isCached ? 'CACHED' : 'FETCHING'})...\r`);
        
        const rows = await fetchSymbolHistoryFromYahoo(symbol);
        
        if (!isCached && rows.length > 0) {
            await sleep(DELAY_MS); 
        }

        if (rows.length < 60) {
            insufficient.push(`${symbol}(${rows.length}d)`);
            continue;
        }

        // 3. Run analysis
        const result = analyzeFibPinball(symbol, rows);
        if (result) {
            allResults.push(result);
        }
    }
    
    console.log('\n\nData loading complete.');
    if (insufficient.length) {
        console.log(`Insufficient data (<60 days): ${insufficient.length} tickers skipped.\n`);
    }

    // 4. Categorise and sort
    const wave1 = allResults.filter(r => r['Wave Position'].includes('Wave 1'));
    const wave3 = allResults.filter(r => r['Wave Position'].includes('Wave 3'));
    const wave5 = allResults.filter(r => r['Wave Position'].includes('Wave 5') || r['Wave Position'].includes('Super Extended'));

    const byConfidence = (a, b) => b.Confidence - a.Confidence || a.Symbol.localeCompare(b.Symbol);
    wave1.sort(byConfidence);
    wave3.sort(byConfidence);
    wave5.sort(byConfidence);

    allResults.sort((a, b) => {
        const order = { 
            'Wave 3': 0, 'Wave 3 Extended': 1, 'Wave 5': 2, 'Wave 5 Extended': 3,
            'Wave 1 of 3': 4, 'Wave 1': 5, 'Early Wave 1 of 3': 6, 'Super Extended': 7 
        };
        const oa = order[a['Wave Position']] ?? 99;
        const ob = order[b['Wave Position']] ?? 99;
        if (oa !== ob) return oa - ob;
        return b.Confidence - a.Confidence;
    });

    // 5. Write output CSVs
    const HEADERS = [
        'Symbol', 'Last Date', 'Wave Position', 'Confidence', 'Description',
        'W0 Low', 'W0 Date', 'W1 High', 'W1 Date', 'W2 Low', 'W2 Date',
        'W2 Retrace %', 'W1 Amplitude', 'Current Price', 'Ext Ratio',
        '0.382 Ext', '0.618 Ext', '0.764 Ext', '1.000 Ext', '1.236 Ext', '1.382 Ext',
        '1.618 Ext', '1.764 Ext', '2.000 Ext',
        'Days Since W2', 'Days Since W0', 'Days of Data'
    ];

    const outAll    = path.join(OUT_DIR, 'fib_pinball_all.csv');
    const outWave1  = path.join(OUT_DIR, 'fib_pinball_wave1.csv');
    const outWave3  = path.join(OUT_DIR, 'fib_pinball_wave3.csv');
    const outWave5  = path.join(OUT_DIR, 'fib_pinball_wave5.csv');

    writeCsv(outAll,    allResults, HEADERS);
    writeCsv(outWave1,  wave1,      HEADERS);
    writeCsv(outWave3,  wave3,      HEADERS);
    writeCsv(outWave5,  wave5,      HEADERS);

    // 6. Summary Display
    console.log('================================================================================');
    console.log(' FIBONACCI PINBALL RESULTS (YAHOO FINANCE)');
    console.log('================================================================================');
    console.log(` Wave 1  (fresh impulse start)  : ${wave1.length.toString().padStart(4)} stocks`); 
    console.log(` Wave 3  (strongest wave)      : ${wave3.length.toString().padStart(4)} stocks`);
    console.log(` Wave 5  (final push)          : ${wave5.length.toString().padStart(4)} stocks`);
    console.log(` Total analysed                : ${allResults.length.toString().padStart(4)} stocks`);
    console.log('================================================================================');

    if (wave3.length > 0) {
        console.log('\nTop Wave 3 stocks (highest confidence):');
        wave3.slice(0, 15).forEach(r => {
            console.log(
                `${r.Symbol.padEnd(16)} Price: ${String(r['Current Price']).padStart(8)} ` +
                `W1-High: ${String(r['W1 High']).padStart(8)} ` +
                `1.618 Target: ${String(r['1.618 Ext']).padStart(8)} ` +
                `Conf: ${r.Confidence}%`
            );
        });
    }

    if (wave5.length > 0) {
        console.log('\nTop Wave 5 stocks (final push):');
        wave5.slice(0, 10).forEach(r => {
            console.log(
                `${r.Symbol.padEnd(16)} Price: ${String(r['Current Price']).padStart(8)} ` +
                `2.000 Target: ${String(r['2.000 Ext']).padStart(8)} ` +
                `Conf: ${r.Confidence}%`
            );
        });
    }

    console.log('\nOutput files generated successfully:');
    console.log(`  ${outAll}`);
    console.log(`  ${outWave1}`);
    console.log(`  ${outWave3}`);
    console.log(`  ${outWave5}`);
}

main().catch(err => { console.error('Fatal:', err); process.exit(1); });
