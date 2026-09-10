const hiddenRawPriceFunds = new Set([
  'SH501312',
  'SH513400',
  'SH513850',
  'SH513290',
  'SZ159502',
  'SZ159577',
  'SZ160140',
  'SZ160416',
  'SZ161126',
  'SZ161127',
  'SZ161128',
  'SZ162415',
  'SZ162719',
  'SZ163208',
])

export function shouldHideValuationRawPrices(symbol: string) {
  return hiddenRawPriceFunds.has(String(symbol || '').trim().toUpperCase())
}
