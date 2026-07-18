#!/usr/bin/env node
'use strict';

/*
 * Defined-risk option-selling analyser (Node.js 18+, no npm packages).
 * It scans only explicitly supplied symbols and proposes an IRON CONDOR
 * candidate: sell an OTM call/put and buy one protective wing on each side.
 *
 * Examples:
 *   node peacefulloption.js --stocks BIOCON
 *   node peacefulloption.js --stocks BIOCON,RELIANCE --otm 0.04 --min-oi 200
 *   node peacefulloption.js --stocks BIOCON --expiry "25-Aug-2026" --json
 *
 * This is an analytical aid, not investment advice or an order-placement tool.
 */

const BASE_URL = 'https://www.nseindia.com';
const OPTION_CHAIN_PAGE = `${BASE_URL}/option-chain`;
const INDEX_SYMBOLS = new Set(['NIFTY', 'BANKNIFTY', 'FINNIFTY', 'MIDCPNIFTY', 'NIFTYNXT50']);
const USER_AGENT = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36';
const WAIT_MS = 1100;

function parseArgs(argv) {
  const options = { symbols: [], expiry: null, otm: 0.04, minOI: 200, json: false };
  for (let i = 0; i < argv.length; i += 1) {
    const arg = argv[i];
    if ((arg === '--symbol' || arg === '--stocks') && argv[i + 1]) {
      options.symbols.push(...argv[++i].split(',').map((s) => s.trim().toUpperCase()).filter(Boolean));
    } else if (arg === '--expiry' && argv[i + 1]) options.expiry = argv[++i];
    else if (arg === '--otm' && argv[i + 1]) options.otm = Number(argv[++i]);
    else if (arg === '--min-oi' && argv[i + 1]) options.minOI = Number(argv[++i]);
    else if (arg === '--json') options.json = true;
    else if (arg === '--help' || arg === '-h') {
      console.log('Usage: node peacefulloption.js --stocks BIOCON[,RELIANCE] [--expiry DD-MMM-YYYY] [--otm 0.04] [--min-oi 200] [--json]');
      process.exit(0);
    } else throw new Error(`Unknown or incomplete argument: ${arg}`);
  }
  options.symbols = [...new Set(options.symbols)];
  if (!options.symbols.length) throw new Error('Pass symbols explicitly, e.g. --stocks BIOCON,RELIANCE');
  if (!(options.otm > 0 && options.otm < 0.25)) throw new Error('--otm must be a decimal between 0 and 0.25 (for example 0.04).');
  if (!(options.minOI >= 0)) throw new Error('--min-oi must be zero or greater.');
  return options;
}

const num = (value) => Number.isFinite(Number(value)) ? Number(value) : 0;
const fixed = (value) => Number(num(value).toFixed(2));
const pause = () => new Promise((resolve) => setTimeout(resolve, WAIT_MS));

function cookieHeader(response) {
  const values = typeof response.headers.getSetCookie === 'function'
    ? response.headers.getSetCookie() : (response.headers.get('set-cookie') || '').split(/,(?=[^;,]+=)/);
  return values.map((value) => value.split(';')[0]).filter(Boolean).join('; ');
}

function headers(cookie = '') {
  return {
    'user-agent': USER_AGENT, accept: 'application/json, text/plain, */*',
    'accept-language': 'en-US,en;q=0.9', referer: OPTION_CHAIN_PAGE,
    ...(cookie ? { cookie } : {}),
  };
}

async function initialiseSession() {
  const response = await fetch(OPTION_CHAIN_PAGE, { headers: headers(), redirect: 'follow' });
  if (!response.ok) throw new Error(`NSE session page returned HTTP ${response.status}`);
  const cookie = cookieHeader(response);
  if (!cookie) throw new Error('NSE did not provide session cookies; try again later.');
  return cookie;
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

async function fetchChain(symbol, requestedExpiry, cookie) {
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

function quote(contract) {
  const oi = num(contract?.openInterest);
  const bid = num(contract?.bidprice ?? contract?.bidPrice);
  const ask = num(contract?.askPrice ?? contract?.askprice);
  if (!contract || oi <= 0) return null;
  if (bid > 0 && ask >= bid) return { bid, ask, oi, source: 'live bid/ask' };
  const ltp = num(contract.lastPrice ?? contract.ltp);
  return ltp > 0 ? { bid: ltp, ask: ltp, oi, source: 'LTP only — verify live quote' } : null;
}

function optionLegs(rows, side, minOI) {
  return rows.map((row) => ({ strike: num(row.strikePrice), quote: quote(row[side]) }))
    .filter((leg) => leg.strike > 0 && leg.quote && leg.quote.oi >= minOI)
    .sort((a, b) => a.strike - b.strike);
}

function maxOIStrike(rows, side) {
  return rows.reduce((best, row) => !row[side] || num(row[side].openInterest) <= num(best?.[side]?.openInterest) ? best : row, null)?.strikePrice ?? null;
}

function buildCondor(rows, spot, options) {
  const calls = optionLegs(rows, 'CE', options.minOI);
  const puts = optionLegs(rows, 'PE', options.minOI);
  const resistance = num(maxOIStrike(rows, 'CE'));
  const support = num(maxOIStrike(rows, 'PE'));
  // The OTM threshold and OI walls both need to be cleared before selling.
  const callFloor = Math.max(spot * (1 + options.otm), resistance);
  const putCeiling = Math.min(spot * (1 - options.otm), support || Infinity);
  const shortCall = calls.find((leg) => leg.strike >= callFloor);
  const shortPut = puts.filter((leg) => leg.strike <= putCeiling).at(-1);
  const longCall = shortCall && calls.find((leg) => leg.strike > shortCall.strike);
  const longPut = shortPut && [...puts].reverse().find((leg) => leg.strike < shortPut.strike);
  if (!shortCall || !longCall || !shortPut || !longPut) {
    return { eligible: false, reason: 'No complete four-leg condor meets the OTM, OI, and hedge-strike requirements.', resistance, support };
  }
  // Conservative expected credit: sell at bid and buy wings at ask.
  const credit = shortCall.quote.bid + shortPut.quote.bid - longCall.quote.ask - longPut.quote.ask;
  const callWidth = longCall.strike - shortCall.strike;
  const putWidth = shortPut.strike - longPut.strike;
  if (credit <= 0 || credit >= Math.min(callWidth, putWidth)) {
    return { eligible: false, reason: 'The available four legs do not provide a valid positive, defined-risk credit.', resistance, support };
  }
  const maxLoss = Math.max(callWidth, putWidth) - credit;
  const allLive = [shortCall, longCall, shortPut, longPut].every((leg) => leg.quote.source === 'live bid/ask');
  return {
    eligible: true, resistance, support, spot, targetOTMPercent: fixed(options.otm * 100),
    shortCall: { strike: shortCall.strike, price: fixed(shortCall.quote.bid), oi: shortCall.quote.oi },
    longCall: { strike: longCall.strike, price: fixed(longCall.quote.ask), oi: longCall.quote.oi },
    shortPut: { strike: shortPut.strike, price: fixed(shortPut.quote.bid), oi: shortPut.quote.oi },
    longPut: { strike: longPut.strike, price: fixed(longPut.quote.ask), oi: longPut.quote.oi },
    netCredit: fixed(credit), maxProfit: fixed(credit), maxLoss: fixed(maxLoss),
    callBreakeven: fixed(shortCall.strike + credit), putBreakeven: fixed(shortPut.strike - credit),
    rewardRisk: fixed(credit / maxLoss), pricing: allLive ? 'live bid/ask' : 'LTP only — verify live quote',
  };
}

function analyse(data, symbol, expiry, options) {
  const rows = data.records.data.filter((row) => (row.expiryDate === expiry || row.expiryDates?.includes(expiry)) && (row.CE || row.PE));
  if (!rows.length) throw new Error(`No contracts found for expiry ${expiry}.`);
  const callOI = rows.reduce((sum, row) => sum + num(row.CE?.openInterest), 0);
  const putOI = rows.reduce((sum, row) => sum + num(row.PE?.openInterest), 0);
  return {
    symbol, expiry, timestamp: data.records.timestamp || new Date().toISOString(), spot: num(data.records.underlyingValue),
    pcr: callOI ? fixed(putOI / callOI) : null, condor: buildCondor(rows, num(data.records.underlyingValue), options),
  };
}

function printReport(report) {
  console.log(`\n${report.symbol} | expiry ${report.expiry} | NSE time ${report.timestamp}`);
  console.log(`Spot ${report.spot} | PCR ${report.pcr ?? 'n/a'}`);
  const c = report.condor;
  console.log(`OI resistance ${c.resistance ?? 'n/a'} | OI support ${c.support ?? 'n/a'}`);
  if (!c.eligible) return console.log(`NO SETUP: ${c.reason}`);
  console.log(`IRON CONDOR CANDIDATE (${c.targetOTMPercent}% minimum OTM):`);
  console.log(`SELL ${report.symbol} ${c.shortCall.strike}CE @ >= ${c.shortCall.price} | BUY ${report.symbol} ${c.longCall.strike}CE @ <= ${c.longCall.price}`);
  console.log(`SELL ${report.symbol} ${c.shortPut.strike}PE @ >= ${c.shortPut.price} | BUY ${report.symbol} ${c.longPut.strike}PE @ <= ${c.longPut.price}`);
  console.log(`Net credit ${c.netCredit} | Max profit ${c.maxProfit} | Max loss ${c.maxLoss} | R:R ${c.rewardRisk}`);
  console.log(`Breakevens: ${c.putBreakeven} to ${c.callBreakeven} | Pricing: ${c.pricing}`);
  console.log('Do not place legs separately without a risk plan. Verify current bid/ask, lot size, margin, event risk, and exit rules before any order.');
}

async function scanOne(symbol, options, cookie) {
  try {
    const { data, expiry } = await fetchChain(symbol, options.expiry, cookie);
    return { report: analyse(data, symbol, expiry, options), cookie };
  } catch (error) {
    if (!/HTTP 401|HTTP 403/.test(error.message)) throw error;
    const freshCookie = await initialiseSession();
    const { data, expiry } = await fetchChain(symbol, options.expiry, freshCookie);
    return { report: analyse(data, symbol, expiry, options), cookie: freshCookie };
  }
}

async function main() {
  const options = parseArgs(process.argv.slice(2));
  let cookie = await initialiseSession();
  const reports = [];
  for (const symbol of options.symbols) {
    try {
      const result = await scanOne(symbol, options, cookie);
      cookie = result.cookie;
      reports.push(result.report);
      if (!options.json) printReport(result.report);
    } catch (error) {
      reports.push({ symbol, error: error.message });
      if (!options.json) console.error(`\n${symbol}: ${error.message}`);
    }
    if (symbol !== options.symbols.at(-1)) await pause();
  }
  if (options.json) console.log(JSON.stringify(reports, null, 2));
}

main().catch((error) => { console.error(`Scanner failed: ${error.message}`); process.exitCode = 1; });
