"""Scale native executed-order evidence; no label and no primary-market identity inference."""
import numpy as np
FEATURE_VERSION='executed_order_evidence_v2'
def execution_features(native,unit,prev_shares,market,limits=None):
 if unit<=0 or prev_shares<=0:raise ValueError('positive unit and previous shares required')
 if not native['audit']['order_features_valid'] or not native['audit']['trade_features_valid']:raise ValueError('invalid native order/trade features')
 e=native['execution'];out={};limits=limits or {}
 for side in ['buy','sell']:
  z=e[side];prefix='v2_'+side+'_';total=z['unit_active']+z['unit_passive'];out[prefix+'unit_U']=total/unit
  for name,value in [('unit',total),('unit_active',z['unit_active']),('unit_passive',z['unit_passive']),('strict_unit',z['strict_unit_executed']),('unknown_unit',z['unknown_unit_executed']),('large_executed',z['large_executed']),('unit_excess',total-z['placebo_executed_mean'])]:out[prefix+name+'_pct']=value/prev_shares*100
  den=z['known_unit_original'];out[prefix+'unit_fill_ratio']=z['known_unit_executed']/den if den else 0;out[prefix+'unit_cancel_ratio']=z['known_unit_cancelled']/den if den else 0;out[prefix+'unit_remaining_ratio']=z['known_unit_remaining']/den if den else 0
  out[prefix+'unit_filled_orders_log']=np.log1p(z['unit_filled_orders']);out[prefix+'unknown_quantity_fraction']=z['unknown_original_executed']/max(z['total'],1)
 out['v2_unit_net_supply_pct']=out['v2_sell_unit_pct']-out['v2_buy_unit_pct'];out['v2_log_previous_U']=np.log1p(prev_shares/unit);out['v2_traded_pct']=e['sell']['total']/prev_shares*100;out['v2_is_sh']=int(market=='SH')
 for key in ['creation','redemption']:
  value=limits.get(key,np.nan);valid=np.isfinite(value) and value>=0 and value<1e14
  out['v2_'+key+'_limit_pct']=value/prev_shares*100 if valid else np.nan;out['v2_'+key+'_limit_known']=int(valid)
 return out
# Absolute U evidence remains visible but is excluded from common-model inputs to avoid scaling twice.
V2_FEATURES=[*sum(([f'v2_{s}_{n}_pct' for n in ['unit','unit_active','unit_passive','strict_unit','unknown_unit','large_executed','unit_excess']]+[f'v2_{s}_{n}' for n in ['unit_fill_ratio','unit_cancel_ratio','unit_remaining_ratio','unit_filled_orders_log','unknown_quantity_fraction']] for s in ['buy','sell']),[]),'v2_unit_net_supply_pct','v2_log_previous_U','v2_traded_pct','v2_is_sh','v2_creation_limit_pct','v2_creation_limit_known','v2_redemption_limit_pct','v2_redemption_limit_known']
