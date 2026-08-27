#!/usr/bin/env node
'use strict';

/*
 * NSE option-spread analyser — Node.js 18+ and no npm packages required.
 * It scans ONLY symbols explicitly passed by the user; it never loops through
 * NIFTY 50 constituents.
 *
 * Examples:
 *   node optionscanner.js --stocks BIOCON
 *   node optionscanner.js --stocks BIOCON,RELIANCE,TCS
 *   node optionscanner.js --symbol BIOCON --expiry "30-Jul-2026" --json
 *
 * Strategy rules from the supplied logic:
 *   PCR < 0.90: bull-call is the preferred debit spread
 *   PCR > 1.10: bear-put is the preferred debit spread
 * Score: reward/risk 40, cost efficiency 30, liquidity/OI 20, bid-ask 10.
 * This is an informational scanner, not investment advice or an order system.
 */

const BASE_URL = 'https://www.nseindia.com';
const OPTION_CHAIN_PAGE = `${BASE_URL}/option-chain`;
const INDEX_SYMBOLS = new Set(['NIFTY', 'BANKNIFTY', 'FINNIFTY', 'MIDCPNIFTY', 'NIFTYNXT50']);
const USER_AGENT = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36';
const WAIT_MS = 1100;

function parseArgs(argv) {
  const options = { symbols: [], expiry: null, json: false };
  for (let i = 0; i < argv.length; i += 1) {
    const arg = argv[i];
    if ((arg === '--symbol' || arg === '--stocks') && argv[i + 1]) {
      options.symbols.push(...argv[++i].split(',').map((s) => s.trim().toUpperCase()).filter(Boolean));
    } else if (arg === '--expiry' && argv[i + 1]) options.expiry = argv[++i];
    else if (arg === '--json') options.json = true;
    else if (arg === '--help' || arg === '-h') {
      console.log('Usage: node optionscanner.js --stocks BIOCON[,RELIANCE] [--expiry DD-MMM-YYYY] [--json]');
      process.exit(0);
    } else throw new Error(`Unknown or incomplete argument: ${arg}`);
  }
  options.symbols = [...new Set(options.symbols)];
  if (!options.symbols.length) throw new Error('Pass one or more stocks, e.g. --stocks BIOCON,RELIANCE');
  return options;
}

function cookieHeader(response) {
  const cookies = typeof response.headers.getSetCookie === 'function'
    ? response.headers.getSetCookie()
    : (response.headers.get('set-cookie') || '').split(/,(?=[^;,]+=)/);
  return cookies.map((value) => value.split(';')[0]).filter(Boolean).join('; ');
}

function headers(cookie = '') {
  return {
    'user-agent': USER_AGENT, accept: 'application/json, text/plain, */*',
    'accept-language': 'en-US,en;q=0.9', referer: OPTION_CHAIN_PAGE,
    ...(cookie ? { cookie } : {}),
  };
}

async function initialiseSession() {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 20000);
  try {
    const response = await fetch(OPTION_CHAIN_PAGE, {
      headers: headers(), redirect: 'follow', signal: controller.signal
    });
    if (!response.ok) throw new Error(`NSE session page returned HTTP ${response.status}`);
    const cookie = cookieHeader(response);
    if (!cookie) throw new Error('NSE did not provide session cookies; try again later.');
    return cookie;
  } catch (error) {
    if (error.name === 'AbortError') throw new Error('NSE session page timed out after 20 seconds');
    throw error;
  } finally { clearTimeout(timeout); }
}

async function getJson(url, cookie, label) {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 20000);
  try {
    const response = await fetch(url, { headers: headers(cookie), signal: controller.signal });
    if (!response.ok) throw new Error(`${label} returned HTTP ${response.status}`);
    return await response.json();
  } catch (error) {
    if (error.name === 'AbortError') throw new Error(`${label} timed out after 20 seconds`);
    throw error;
  } finally { clearTimeout(timeout); }
}

async function fetchSymbol(symbol, requestedExpiry, cookie) {
  const info = await getJson(`${BASE_URL}/api/option-chain-contract-info?symbol=${encodeURIComponent(symbol)}`, cookie, 'NSE contract-info API');
  const expiry = requestedExpiry || info.expiryDates?.[0];
  if (!expiry || !info.expiryDates?.includes(expiry)) {
    throw new Error(`Expiry "${expiry}" unavailable. Available: ${(info.expiryDates || []).slice(0, 8).join(', ')}`);
  }
  const type = INDEX_SYMBOLS.has(symbol) ? 'Indices' : 'Equity';
  const url = `${BASE_URL}/api/option-chain-v3?type=${type}&symbol=${encodeURIComponent(symbol)}&expiry=${encodeURIComponent(expiry)}`;
  const data = await getJson(url, cookie, 'NSE option-chain API');
  if (!data?.records?.data?.length) throw new Error('NSE returned no option-chain rows.');
  return { data, expiry };
}

async function fetchIndiaVix(cookie) {
  const payload = await getJson(`${BASE_URL}/api/allIndices`, cookie, 'NSE all-indices API');
  const vix = (payload.data || []).find((item) => String(item.indexSymbol || item.index || '').toUpperCase() === 'INDIA VIX');
  if (!vix) return null;
  return num(vix.last ?? vix.lastPrice ?? vix.indexValue);
}

const num = (value) => Number.isFinite(Number(value)) ? Number(value) : 0;
const fixed = (value) => Number(num(value).toFixed(2));
const compact = (value) => new Intl.NumberFormat('en-IN', { maximumFractionDigits: 0 }).format(num(value));
const pause = () => new Promise((resolve) => setTimeout(resolve, WAIT_MS));

function optionQuote(contract) {
  const bid = num(contract?.bidprice ?? contract?.bidPrice);
  const ask = num(contract?.askPrice ?? contract?.askprice);
  const oi = num(contract?.openInterest);
  if (!contract || oi <= 0) return null;
  if (bid > 0 && ask > 0 && ask < bid) return null;
  if (bid > 0 && ask > 0 && ask >= bid) {
    return { bid, ask, oi, relativeSpread: (ask - bid) / ((ask + bid) / 2), priceSource: 'live bid/ask' };
  }
  // NSE often has no two-sided quote after close. LTP allows an indicative scan,
  // but output is explicitly marked so it cannot be treated as an executable price.
  const ltp = num(contract.lastPrice ?? contract.ltp);
  if (ltp <= 0) return null;
  return { bid: ltp, ask: ltp, oi, relativeSpread: 0.10, priceSource: 'last traded price (verify live quote)' };
}

function scoreSpread(spread, maxAverageOI) {
  const rr = spread.maxLoss > 0 ? spread.maxProfit / spread.maxLoss : 0;
  const rewardRisk = Math.min(rr / 2, 1) * 40;              // 2:1 receives full 40
  const costEfficiency = Math.max(0, 1 - spread.debit / spread.width) * 30;
  const liquidity = Math.min(spread.averageOI / maxAverageOI, 1) * 20;
  const bidAsk = Math.max(0, 1 - spread.relativeSpread / 0.15) * 10; // 15% is poor
  return fixed(rewardRisk + costEfficiency + liquidity + bidAsk);
}

function buildSpreads(rows, side, spot) {
  const legs = rows
    .map((row) => ({ strike: num(row.strikePrice), quote: optionQuote(row[side]) }))
    .filter((leg) => leg.quote && leg.strike > 0)
    // Ignore very distant strikes where apparent premiums and quotes are often stale.
    .filter((leg) => leg.strike >= spot * 0.80 && leg.strike <= spot * 1.20)
    .sort((a, b) => a.strike - b.strike);
  const candidates = [];
  const maxWidth = Math.max(spot * 0.10, 1);

  for (let low = 0; low < legs.length; low += 1) {
    for (let high = low + 1; high < legs.length; high += 1) {
      const lower = legs[low], higher = legs[high];
      const width = higher.strike - lower.strike;
      if (width > maxWidth) break;
      // Conservative executable prices: buy at ask and sell at bid.
      const buy = side === 'CE' ? lower : higher;
      const sell = side === 'CE' ? higher : lower;
      const debit = buy.quote.ask - sell.quote.bid;
      if (debit <= 0 || debit >= width) continue;
      candidates.push({
        strategy: side === 'CE' ? 'Bull Call Spread' : 'Bear Put Spread',
        buyStrike: buy.strike, sellStrike: sell.strike, width, debit,
        buyPrice: buy.quote.ask, sellPrice: sell.quote.bid,
        maxProfit: width - debit, maxLoss: debit,
        averageOI: (buy.quote.oi + sell.quote.oi) / 2,
        relativeSpread: (buy.quote.relativeSpread + sell.quote.relativeSpread) / 2,
        priceSource: buy.quote.priceSource === 'live bid/ask' && sell.quote.priceSource === 'live bid/ask'
          ? 'live bid/ask' : 'last traded price (verify live quote)',
      });
    }
  }
  const maxAverageOI = Math.max(...candidates.map((item) => item.averageOI), 1);
  return candidates.map((item) => ({
    ...item,
    buyPrice: fixed(item.buyPrice), sellPrice: fixed(item.sellPrice),
    debit: fixed(item.debit), maxProfit: fixed(item.maxProfit), maxLoss: fixed(item.maxLoss),
    rewardRisk: fixed(item.maxProfit / item.maxLoss),
    bidAskPercent: fixed(item.relativeSpread * 100),
    score: scoreSpread(item, maxAverageOI),
  })).sort((a, b) => b.score - a.score).slice(0, 3);
}

function analyse(data, symbol, expiry, indiaVix) {
  const rows = data.records.data.filter((row) =>
    (row.expiryDate === expiry || row.expiryDates?.includes(expiry)) && (row.CE || row.PE));
  if (!rows.length) throw new Error(`No contracts found for expiry ${expiry}.`);
  let callOI = 0, putOI = 0;
  for (const row of rows) { callOI += num(row.CE?.openInterest); putOI += num(row.PE?.openInterest); }
  const pcr = callOI ? putOI / callOI : null;
  const bias = pcr == null ? 'Unavailable' : pcr < 0.9 ? 'Mildly bullish (bull call preferred)' : pcr > 1.1 ? 'Mildly bearish (bear put preferred)' : 'Neutral';
  const spot = num(data.records.underlyingValue);
  // Show both defined-risk alternatives; PCR labels one as preferred rather than
  // suppressing the other. This is useful for comparing the actual risk/reward.
  const bullSpreads = buildSpreads(rows, 'CE', spot);
  const bearSpreads = buildSpreads(rows, 'PE', spot);
  return {
    symbol, expiry, timestamp: data.records.timestamp || new Date().toISOString(), spot,
    pcr: pcr == null ? null : fixed(pcr), callOI, putOI, indiaVix, bias,
    bull: bullSpreads[0] || null, bear: bearSpreads[0] || null,
  };
}

function ticket(symbol, spread, preference) {
  if (!spread) return `${preference}: no liquid, defined-risk spread found.`;
  const optionType = spread.strategy === 'Bull Call Spread' ? 'CE' : 'PE';
  const breakeven = spread.strategy === 'Bull Call Spread'
    ? fixed(spread.buyStrike + spread.debit) : fixed(spread.buyStrike - spread.debit);
  const isLive = spread.priceSource === 'live bid/ask';
  const buyLimit = isLive ? `<= ${spread.buyPrice}` : `~ ${spread.buyPrice}`;
  const sellLimit = isLive ? `>= ${spread.sellPrice}` : `~ ${spread.sellPrice}`;
  return [
    `${preference} ${spread.strategy}: BUY ${symbol} ${spread.buyStrike}${optionType} @ ${buyLimit}; SELL ${symbol} ${spread.sellStrike}${optionType} @ ${sellLimit}`,
    `net debit ${fixed(spread.debit)}; max profit ${spread.maxProfit}; max loss ${spread.maxLoss}; breakeven ${breakeven}; R:R ${spread.rewardRisk}; score ${spread.score}/100`,
    `pricing: ${spread.priceSource}`,
  ].join(' | ');
}

function printReport(report) {
  console.log(`\n${report.symbol} | expiry ${report.expiry} | NSE time ${report.timestamp}`);
  console.log(`Spot ${fixed(report.spot)} | PCR ${report.pcr ?? 'n/a'} | India VIX ${report.indiaVix ?? 'n/a'} | ${report.bias}`);
  const bullPreferred = report.pcr != null && report.pcr < 0.9 ? 'PREFERRED:' : 'ALTERNATIVE:';
  const bearPreferred = report.pcr != null && report.pcr > 1.1 ? 'PREFERRED:' : 'ALTERNATIVE:';
  console.log(ticket(report.symbol, report.bull, bullPreferred));
  console.log(ticket(report.symbol, report.bear, bearPreferred));
  console.log('Indicative analysis only. Verify a live two-sided quote, lot size, margins, liquidity, and risk before placing any order.');
}

async function scanOne(symbol, options, cookie) {
  try {
    const { data, expiry } = await fetchSymbol(symbol, options.expiry, cookie);
    return { report: analyse(data, symbol, expiry), cookie };
  } catch (error) {
    if (!/HTTP 401|HTTP 403/.test(error.message)) throw error;
    const freshCookie = await initialiseSession();
    const { data, expiry } = await fetchSymbol(symbol, options.expiry, freshCookie);
    return { report: analyse(data, symbol, expiry), cookie: freshCookie };
  }
}

async function main() {
  const options = parseArgs(process.argv.slice(2));
  let cookie = await initialiseSession();
  let indiaVix = null;
  try { indiaVix = await fetchIndiaVix(cookie); } catch { /* VIX is context, not a scan blocker. */ }
  const reports = [];
  for (const symbol of options.symbols) {
    try {
      const result = await scanOne(symbol, options, cookie);
      cookie = result.cookie;
      result.report.indiaVix = indiaVix;
      reports.push(result.report);
      if (!options.json) printReport(result.report);
    } catch (error) {
      const failure = { symbol, error: error.message };
      reports.push(failure);
      if (!options.json) console.error(`\n${symbol}: ${error.message}`);
    }
    if (symbol !== options.symbols.at(-1)) await pause();
  }
  if (options.json) console.log(JSON.stringify(reports, null, 2));
}

main().catch((error) => { console.error(`Scanner failed: ${error.message}`); process.exitCode = 1; });
