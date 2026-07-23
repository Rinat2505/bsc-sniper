#!/usr/bin/env python3
"""BSC PancakeSwap Sniper — "second wave" strategy.
Entry: 45s after new token launch (after bot-dump), if price still alive.
DRY-RUN by default. Set dry_run=false in config for LIVE.
"""

import json, time, os, sys, random, urllib.request, urllib.error
from datetime import datetime, timezone

HERE          = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH   = os.path.join(HERE, "sniper_config.json")
KEYS_PATH     = os.path.join(HERE, "keys.json")
HISTORY_PATH  = os.path.join(HERE, "sniper_history.json")
POS_PATH      = os.path.join(HERE, "sniper_positions.json")
LOG_PATH      = os.path.join(HERE, "sniper_stdout.txt")

FACTORY_ADDR = "0xcA143Ce32Fe78f1f7019d7d551a6402fC5350c73"
ROUTER_ADDR  = "0x10ED43C718714eb63d5aA57B78B54704E256024E"
WBNB_ADDR    = "0xbb4CdB9CBd36B01bD1cBaEBF2De08d9173bc095c"

FACTORY_ABI = [{"anonymous":False,"inputs":[
    {"indexed":True,"name":"token0","type":"address"},
    {"indexed":True,"name":"token1","type":"address"},
    {"indexed":False,"name":"pair","type":"address"},
    {"indexed":False,"name":"","type":"uint256"}
],"name":"PairCreated","type":"event"}]

PAIR_ABI = [
    {"constant":True,"inputs":[],"name":"getReserves","outputs":[
        {"name":"_reserve0","type":"uint112"},{"name":"_reserve1","type":"uint112"},
        {"name":"_blockTimestampLast","type":"uint32"}],"type":"function"},
    {"constant":True,"inputs":[],"name":"token0","outputs":[{"name":"","type":"address"}],"type":"function"},
    {"constant":True,"inputs":[],"name":"token1","outputs":[{"name":"","type":"address"}],"type":"function"},
]

ROUTER_ABI = [
    {"inputs":[{"name":"amountOutMin","type":"uint256"},{"name":"path","type":"address[]"},
               {"name":"to","type":"address"},{"name":"deadline","type":"uint256"}],
     "name":"swapExactETHForTokens","outputs":[{"name":"amounts","type":"uint256[]"}],
     "stateMutability":"payable","type":"function"},
    {"inputs":[{"name":"amountIn","type":"uint256"},{"name":"amountOutMin","type":"uint256"},
               {"name":"path","type":"address[]"},{"name":"to","type":"address"},
               {"name":"deadline","type":"uint256"}],
     "name":"swapExactTokensForETH","outputs":[{"name":"amounts","type":"uint256[]"}],
     "stateMutability":"nonpayable","type":"function"},
    {"inputs":[{"name":"amountIn","type":"uint256"},{"name":"path","type":"address[]"}],
     "name":"getAmountsOut","outputs":[{"name":"amounts","type":"uint256[]"}],
     "stateMutability":"view","type":"function"},
    {"inputs":[{"name":"amountOutMin","type":"uint256"},{"name":"path","type":"address[]"},
               {"name":"to","type":"address"},{"name":"deadline","type":"uint256"}],
     "name":"swapExactETHForTokensSupportingFeeOnTransferTokens","outputs":[],
     "stateMutability":"payable","type":"function"},
    {"inputs":[{"name":"amountIn","type":"uint256"},{"name":"amountOutMin","type":"uint256"},
               {"name":"path","type":"address[]"},{"name":"to","type":"address"},
               {"name":"deadline","type":"uint256"}],
     "name":"swapExactTokensForETHSupportingFeeOnTransferTokens","outputs":[],
     "stateMutability":"nonpayable","type":"function"},
]

ERC20_ABI = [
    {"constant":True,"inputs":[{"name":"_owner","type":"address"}],"name":"balanceOf",
     "outputs":[{"name":"balance","type":"uint256"}],"type":"function"},
    {"constant":False,"inputs":[{"name":"_spender","type":"address"},{"name":"_value","type":"uint256"}],
     "name":"approve","outputs":[{"name":"","type":"bool"}],"type":"function"},
    {"constant":True,"inputs":[{"name":"_owner","type":"address"},{"name":"_spender","type":"address"}],
     "name":"allowance","outputs":[{"name":"","type":"uint256"}],"type":"function"},
    {"constant":True,"inputs":[],"name":"decimals","outputs":[{"name":"","type":"uint8"}],"type":"function"},
    {"constant":True,"inputs":[],"name":"symbol","outputs":[{"name":"","type":"string"}],"type":"function"},
    {"constant":True,"inputs":[],"name":"totalSupply","outputs":[{"name":"","type":"uint256"}],"type":"function"},
]

# Keccak4 (первые 4 байта) для функций, которые сигнализируют об опасности контракта.
# mint(address,uint256) = инфляция токенов на любой адрес по желанию дева
# mint(uint256) = та же угроза, другая сигнатура
# pause() = владелец может заморозить торговлю мгновенно
# Источник: arXiv 2403.01425 — mint + pause = в 100% случаев malicious в датасете
_DANGER_SELECTORS = {
    "40c10f19": "mint(address,uint256)",
    "a0712d68": "mint(uint256)",
    "8456cb59": "pause()",
}

# PancakeSwap V2 Swap event topic (= Uniswap V2, одинаковая сигнатура)
SWAP_TOPIC = "0xd78ad95fa46c994b6551d0da85fc275fe613ce37657fb8d5e3d130840159d822"

PAIR_SWAP_ABI = [{"anonymous": False, "inputs": [
    {"indexed": True,  "name": "sender",     "type": "address"},
    {"indexed": False, "name": "amount0In",  "type": "uint256"},
    {"indexed": False, "name": "amount1In",  "type": "uint256"},
    {"indexed": False, "name": "amount0Out", "type": "uint256"},
    {"indexed": False, "name": "amount1Out", "type": "uint256"},
    {"indexed": True,  "name": "to",         "type": "address"},
], "name": "Swap", "type": "event"}]

# Honeypot check: Binance hot wallet (много BNB, подходит для eth_call симуляции)
_HP_WHALE = "0x8894E0a0c962CB723c1976a4421c95949bE2D4E5"
# Максимальный суммарный налог токена (buy+sell), при превышении — пропускаем
_MAX_ROUNDTRIP_TAX_PCT = 20.0

# ABI для чтения налога из популярных паттернов BSC-мем-токенов
_TAX_READER_ABI = [
    {"name": fn, "type": "function", "inputs": [],
     "outputs": [{"name": "", "type": "uint256"}], "stateMutability": "view"}
    for fn in ["_taxFee","totalFees","buyFee","sellFee","buyTax","sellTax",
               "_fee","totalTax","transferFee","reflectionFee","_totalFee",
               "taxFee","liquidityFee","marketingFee","burnFee"]
]

def check_goplus(token_addr):
    """GoPlus Security API — 30+ проверок. Timeout 8с, при ошибке пропускаем (не блокируем)."""
    url = f"https://api.gopluslabs.io/api/v1/token_security/56?contract_addresses={token_addr}"
    try:
        req = urllib.request.Request(url, headers={"Accept": "application/json", "User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=8) as r:
            data = json.loads(r.read())
        info = (data.get("result") or {})
        info = info.get(token_addr.lower()) or info.get(token_addr) or {}
        if not info:
            return True, "goplus:no_data"
        for field in ("is_honeypot", "is_mintable", "can_take_back_ownership",
                      "transfer_pausable", "hidden_owner", "selfdestruct", "owner_change_balance"):
            if info.get(field) == "1":
                return False, f"goplus:{field}"
        try:
            if float(info.get("sell_tax") or 0) > 10:
                return False, f"goplus:sell_tax={info['sell_tax']}%"
            if float(info.get("buy_tax") or 0) > 5:
                return False, f"goplus:buy_tax={info['buy_tax']}%"
            top10 = float(info.get("top10_holder_rate") or 0)
            if top10 > 0.80:
                return False, f"goplus:top10={top10*100:.0f}%"
        except (ValueError, TypeError):
            pass
        # holder_count: <10 = только дев и пара ботов, признак ранней манипуляции
        try:
            hc = int(info.get("holder_count") or 0)
            if 0 < hc < 10:
                return False, f"goplus:holder_count={hc}"
        except (ValueError, TypeError):
            pass
        # LP lock: если <30% LP сожжено/залочено — дев может руговать в любой момент
        lp_holders = info.get("lp_holders") or []
        if lp_holders:
            _dead = {"0x000000000000000000000000000000000000dead",
                     "0x0000000000000000000000000000000000000000"}
            safe_lp = sum(float(h.get("percent", 0))
                          for h in lp_holders
                          if h.get("address", "").lower() in _dead
                          or h.get("is_locked") == 1)
            if safe_lp < 0.30:
                return False, f"goplus:lp_unlocked={safe_lp*100:.0f}%_safe"
        return True, "goplus:ok"
    except Exception:
        return True, "goplus:timeout"

def check_honeypot_is(token_addr):
    """honeypot.is — симуляция buy+sell. Timeout 8с, при ошибке пропускаем."""
    url = f"https://api.honeypot.is/v2/IsHoneypot?address={token_addr}&chainID=56"
    try:
        req = urllib.request.Request(url, headers={"Accept": "application/json", "User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=8) as r:
            data = json.loads(r.read())
        if (data.get("honeypotResult") or {}).get("isHoneypot"):
            reason = (data.get("honeypotResult") or {}).get("honeypotReason", "honeypot")
            return False, f"hpis:{reason}"
        risk = (data.get("summary") or {}).get("riskLevel", 0)
        if risk > 70:
            return False, f"hpis:risk={risk}"
        sell_tax = (data.get("simulationResult") or {}).get("sellTax", 0)
        if sell_tax > 10:
            return False, f"hpis:sellTax={sell_tax:.1f}%"
        return True, "hpis:ok"
    except Exception:
        return True, "hpis:timeout"

RPC_URLS = [
    "https://bsc.rpc.blxrbdn.com",                              # BloxRoute — надёжный getLogs
    "https://bsc-mainnet.public.blastapi.io",                   # BlastAPI — хороший rate limit
    "https://bsc.meowrpc.com",                                  # MeowRPC — публичный без limits
    "https://endpoints.omniatech.io/v1/bsc/mainnet/public",     # Omnia — стабильный
    "https://bsc-rpc.publicnode.com",                           # publicnode (иногда 403)
    "https://bnb.api.onfinality.io/public",                     # onfinality (иногда 429)
]

_log_file = None

def log(msg):
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    line = f"[{ts}] {msg}"
    print(line, flush=True)
    if _log_file:
        try:
            _log_file.write(line + "\n")
            _log_file.flush()
        except Exception:
            pass

def load_cfg():
    with open(CONFIG_PATH, encoding="utf-8") as f:
        return json.load(f)

def load_history():
    try:
        with open(HISTORY_PATH, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []

def save_history(h):
    content = json.dumps(h, ensure_ascii=False, indent=2)
    import io
    with io.open(HISTORY_PATH, "w", encoding="utf-8", newline="\n") as f:
        f.write(content)

def load_positions():
    try:
        with open(POS_PATH, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}

def save_positions(p):
    content = json.dumps(p, ensure_ascii=False, indent=2)
    import io
    with io.open(POS_PATH, "w", encoding="utf-8", newline="\n") as f:
        f.write(content)

def tg_send(token, chat_id, text):
    import urllib.request
    try:
        body = json.dumps({"chat_id": chat_id, "text": text, "parse_mode": "HTML"}).encode()
        req = urllib.request.Request(
            f"https://api.telegram.org/bot{token}/sendMessage",
            data=body, headers={"Content-Type": "application/json"}
        )
        urllib.request.urlopen(req, timeout=10)
    except Exception as e:
        log(f"[TG] {e}")

_rpc_cooldown = {}  # url → cooldown-until timestamp
_cur_rpc_url  = [None]  # mutable box for current RPC url

def get_web3(bad_url=None):
    try:
        from web3 import Web3
        from web3.middleware import ExtraDataToPOAMiddleware
    except ImportError:
        log("[ERR] web3 не установлен: pip install web3")
        sys.exit(1)
    if bad_url:
        _rpc_cooldown[bad_url] = time.time() + 120  # 2 мин cooldown для сбойного URL
    now = time.time()
    urls = [u for u in RPC_URLS if now >= _rpc_cooldown.get(u, 0)]
    if not urls:
        urls = list(RPC_URLS)  # все на cooldown — сбрасываем
    # blxrbdn первым — самый стабильный для eth_getLogs
    primary = "https://bsc.rpc.blxrbdn.com"
    if primary in urls:
        urls = [primary] + [u for u in urls if u != primary]
    for url in urls:
        try:
            w3 = Web3(Web3.HTTPProvider(url, request_kwargs={"timeout": 8}))
            if w3.is_connected():
                w3.middleware_onion.inject(ExtraDataToPOAMiddleware, layer=0)
                _cur_rpc_url[0] = url
                log(f"[RPC] подключён к {url[:45]}")
                return w3
        except Exception as e:
            log(f"[RPC] {url[:45]} — {e}")
    raise RuntimeError("Нет подключения к BSC RPC")

def get_price(w3, pair_addr, is_token0_bnb):
    from web3 import Web3
    try:
        pair = w3.eth.contract(address=Web3.to_checksum_address(pair_addr), abi=PAIR_ABI)
        r = pair.functions.getReserves().call()
        r0, r1 = r[0], r[1]
        if r0 == 0 or r1 == 0:
            return None
        bnb_r = r0 if is_token0_bnb else r1
        tok_r = r1 if is_token0_bnb else r0
        return bnb_r / tok_r  # BNB per token
    except Exception:
        return None

def get_symbol(w3, token_addr):
    from web3 import Web3
    try:
        t = w3.eth.contract(address=Web3.to_checksum_address(token_addr), abi=ERC20_ABI)
        return t.functions.symbol().call()
    except Exception:
        return token_addr[:8]

# ── DRY-RUN simulation helpers ────────────────────────────────────────────────

_FEE_BPS   = 25      # PancakeSwap V2: 0.25 %
_FEE_DENOM = 10000

def _get_reserves(w3, pair_addr, is_token0_bnb):
    """(bnb_r_wei, tok_r_raw) или (None, None)."""
    from web3 import Web3
    try:
        pair = w3.eth.contract(address=Web3.to_checksum_address(pair_addr), abi=PAIR_ABI)
        r = pair.functions.getReserves().call()
        bnb_r = r[0] if is_token0_bnb else r[1]
        tok_r = r[1] if is_token0_bnb else r[0]
        return (bnb_r, tok_r) if bnb_r and tok_r else (None, None)
    except Exception:
        return None, None

def _amm_buy(bnb_r, tok_r, bnb_float):
    """Constant-product AMM: BNB → tokens. Возвращает (tokens_raw, new_bnb_r, new_tok_r)."""
    bnb_in  = int(bnb_float * 1e18)
    adj     = bnb_in - (bnb_in * _FEE_BPS // _FEE_DENOM)
    tokens  = tok_r * adj // (bnb_r + adj)
    return tokens, bnb_r + adj, tok_r - tokens

def _amm_sell(bnb_r, tok_r, tokens_in):
    """Constant-product AMM: tokens → BNB. Возвращает (bnb_float, new_bnb_r, new_tok_r)."""
    adj    = tokens_in - (tokens_in * _FEE_BPS // _FEE_DENOM)
    bnb_w  = bnb_r * adj // (tok_r + adj)
    return bnb_w / 1e18, bnb_r - bnb_w, tok_r + adj

def _impact(r_in_b, r_out_b, r_in_a, r_out_a):
    """Ценовой impact %."""
    return abs(r_in_a / r_out_a - r_in_b / r_out_b) / (r_in_b / r_out_b) * 100

def _gas(cfg, key):
    return cfg.get("sim_gas_price_gwei", 3) * cfg.get(key, 180000) * 1e-9

def _latency(cfg):
    return random.uniform(cfg.get("sim_tx_latency_min_sec", 3),
                          cfg.get("sim_tx_latency_max_sec", 12))

def _gas_dynamic(w3, cfg, gas_limit_key):
    """Реальная цена газа с сети + 15% буфер (реалистичнее фиксированных 3 Gwei)."""
    try:
        gwei = w3.eth.gas_price / 1e9 * 1.15
        gwei = max(min(gwei, 15.0), 1.0)  # зажимаем в [1, 15] Gwei
    except Exception:
        gwei = cfg.get("sim_gas_price_gwei", 3)
    return gwei * cfg.get(gas_limit_key, 180000) * 1e-9

def _mev_penalty():
    """MEV-боты сэндвичат новые пары. Симулируем потерю 0.3-2.5% на покупке."""
    return random.uniform(0.003, 0.025)

def analyze_pair_swaps(w3, pair_addr, is_token0_bnb, from_block, to_block):
    """
    Анализирует Swap-события пары с момента создания.
    Возвращает (buy_vol_bnb, sell_vol_bnb, buy_cnt, sell_cnt, max_single_sell_bnb).

    Определение:
      - buy:  BNB идёт В пул (amount0In/amount1In > 0), токены выходят
      - sell: BNB выходит ИЗ пула (amount0Out/amount1Out > 0), токены входят
    """
    from web3 import Web3
    try:
        pair_c = w3.eth.contract(
            address=Web3.to_checksum_address(pair_addr), abi=PAIR_SWAP_ABI)
        blk_range = min(to_block - from_block, 60)  # не более 60 блоков (~3 мин)
        logs = w3.eth.get_logs({
            "fromBlock": hex(to_block - blk_range),
            "toBlock":   hex(to_block),
            "address":   Web3.to_checksum_address(pair_addr),
            "topics":    [SWAP_TOPIC],
        })
        buy_vol = sell_vol = max_sell = 0.0
        buy_cnt = sell_cnt = 0
        for evt in logs:
            a = pair_c.events.Swap().process_log(evt)["args"]
            if is_token0_bnb:
                bnb_in  = a["amount0In"]  / 1e18
                bnb_out = a["amount0Out"] / 1e18
            else:
                bnb_in  = a["amount1In"]  / 1e18
                bnb_out = a["amount1Out"] / 1e18
            if bnb_in > 0.0001:
                buy_vol += bnb_in
                buy_cnt += 1
            if bnb_out > 0.0001:
                sell_vol += bnb_out
                sell_cnt += 1
                max_sell = max(max_sell, bnb_out)
        return buy_vol, sell_vol, buy_cnt, sell_cnt, max_sell
    except Exception:
        return 0.0, 0.0, 0, 0, 0.0

def check_contract_safety(w3, token_addr, tok_r_raw):
    """
    Два on-chain check'а перед входом (дополнительно к honeypot):

    1. Supply concentration: какой % от totalSupply сейчас в LP?
       Если дев держит 90%+ токенов вне пула — он может скинуть их в любой момент.
       Источник: JayArrowz/PancakeTokenSniper (MinimumPercentageOfTokenInLiquidityPool),
                 arXiv 2206.08202 (creator concentration = главный признак rug pull).

    2. Bytecode danger functions: проверяем bytecode на функции mint() и pause().
       mint(address,uint256) = дев может создавать токены на любой адрес (inflate supply).
       pause() = дев может остановить торговлю в момент выхода.
       Источник: arXiv 2403.01425 — mint+pause = 100% malicious в датасете.

    Возвращает (is_safe, warning_note).
    is_safe=False = пропустить токен.
    """
    from web3 import Web3
    token_cs = Web3.to_checksum_address(token_addr)
    notes = []

    # ── 1. Supply concentration ────────────────────────────────────────────────
    try:
        tok_c = w3.eth.contract(address=token_cs, abi=ERC20_ABI)
        total_supply = tok_c.functions.totalSupply().call()
        if total_supply > 0 and tok_r_raw > 0:
            in_lp_pct = tok_r_raw / total_supply * 100
            if in_lp_pct < 10.0:
                # Дев держит >90% supply — может сбросить и обрушить цену
                return False, f"supply в LP: {in_lp_pct:.1f}% (дев держит {100-in_lp_pct:.0f}%)"
            notes.append(f"LP={in_lp_pct:.0f}%")
    except Exception:
        pass

    # ── 2. Bytecode dangerous functions ───────────────────────────────────────
    try:
        code_hex = w3.eth.get_code(token_cs).hex()
        found = [name for sel, name in _DANGER_SELECTORS.items() if sel in code_hex]
        if len(found) >= 2:
            # Два и более опасных метода = высокая вероятность malicious
            return False, f"опасный bytecode: {', '.join(found)}"
        if found:
            notes.append(f"⚠{found[0]}")
    except Exception:
        pass

    return True, " | ".join(notes) if notes else ""

def check_token(w3, router, token_addr):
    """
    Honeypot-детект + чтение налога из контракта.
    Возвращает (is_ok, buy_tax_pct, sell_tax_pct).
    is_ok=False если токен нельзя купить/продать (honeypot).
    """
    from web3 import Web3
    token_cs = Web3.to_checksum_address(token_addr)

    # ── 1. Honeypot check: симулируем покупку через eth_call ──────────────────
    try:
        router.functions.swapExactETHForTokensSupportingFeeOnTransferTokens(
            0,  # amountOutMin=0 — принимаем любой результат
            [Web3.to_checksum_address(WBNB_ADDR), token_cs],
            _HP_WHALE,
            int(time.time()) + 60,
        ).call({"from": _HP_WHALE, "value": Web3.to_wei(0.001, "ether")})
    except Exception as e:
        err = str(e).lower()
        # Явные признаки honeypot или антибота
        if any(x in err for x in ("blacklist", "bot", "paused", "not allowed",
                                   "forbidden", "transfer_failed", "locked",
                                   "trading not", "not started")):
            return False, 0.0, 0.0
        # Остальные ошибки (K, insufficient, expired и т.д.) — норма AMM, не honeypot

    # ── 2. Чтение налога из контракта ─────────────────────────────────────────
    token_c = w3.eth.contract(address=token_cs, abi=_TAX_READER_ABI)

    def _read_tax(fn_names):
        for fn in fn_names:
            try:
                val = getattr(token_c.functions, fn)().call()
                if 0 < val <= 5000:  # до 50%
                    # val либо в basis points (500=5%) либо в процентах (5=5%)
                    return float(val) / 100.0 if val > 50 else float(val)
            except Exception:
                continue
        return 0.0

    buy_tax  = _read_tax(["buyTax", "buyFee", "_taxFee", "totalFees",
                           "_fee", "totalTax", "transferFee", "taxFee"])
    sell_tax = _read_tax(["sellTax", "sellFee", "_sellFee", "burnFee",
                           "liquidityFee", "marketingFee"])

    # Если нашли только один тип — применяем к обоим
    if buy_tax > 0 and sell_tax == 0:
        sell_tax = buy_tax
    elif sell_tax > 0 and buy_tax == 0:
        buy_tax = sell_tax

    return True, buy_tax, sell_tax

def buy_live(w3, router, account, token_addr, bnb_amount, cfg):
    from web3 import Web3
    try:
        slippage = 1 - cfg.get("max_slippage_bps", 9900) / 10000
        amounts = router.functions.getAmountsOut(
            w3.to_wei(bnb_amount, "ether"),
            [Web3.to_checksum_address(WBNB_ADDR), Web3.to_checksum_address(token_addr)]
        ).call()
        min_out = int(amounts[1] * slippage)

        gas_price = int(w3.eth.gas_price * 1.15)
        tx = router.functions.swapExactETHForTokens(
            min_out,
            [Web3.to_checksum_address(WBNB_ADDR), Web3.to_checksum_address(token_addr)],
            account.address,
            int(time.time()) + 60
        ).build_transaction({
            "from": account.address,
            "value": w3.to_wei(bnb_amount, "ether"),
            "gas": 300000,
            "gasPrice": gas_price,
            "nonce": w3.eth.get_transaction_count(account.address),
        })
        signed = account.sign_transaction(tx)
        txhash = w3.eth.send_raw_transaction(signed.raw_transaction)
        receipt = w3.eth.wait_for_transaction_receipt(txhash, timeout=60)
        if receipt.status == 1:
            token_c = w3.eth.contract(address=Web3.to_checksum_address(token_addr), abi=ERC20_ABI)
            bal = token_c.functions.balanceOf(account.address).call()
            log(f"[✓] Куплено {bal} токенов | tx: {txhash.hex()[:16]}...")
            return bal
    except Exception as e:
        log(f"[ERR] buy_live: {e}")
    return None

def sell_live(w3, router, account, token_addr, token_amount, cfg):
    from web3 import Web3
    try:
        token_c = w3.eth.contract(address=Web3.to_checksum_address(token_addr), abi=ERC20_ABI)
        allowance = token_c.functions.allowance(account.address, Web3.to_checksum_address(ROUTER_ADDR)).call()
        if allowance < token_amount:
            approve_tx = token_c.functions.approve(
                Web3.to_checksum_address(ROUTER_ADDR), 2**256 - 1
            ).build_transaction({
                "from": account.address, "gas": 100000,
                "gasPrice": w3.eth.gas_price,
                "nonce": w3.eth.get_transaction_count(account.address),
            })
            signed = account.sign_transaction(approve_tx)
            w3.eth.send_raw_transaction(signed.raw_transaction)
            time.sleep(4)

        sell_amounts = router.functions.getAmountsOut(
            token_amount,
            [Web3.to_checksum_address(token_addr), Web3.to_checksum_address(WBNB_ADDR)]
        ).call()
        sell_slippage = 1 - cfg.get("sell_slippage_bps", 500) / 10000
        min_bnb_out = int(sell_amounts[1] * sell_slippage)

        bnb_before = w3.eth.get_balance(account.address)
        tx = router.functions.swapExactTokensForETH(
            token_amount, min_bnb_out,
            [Web3.to_checksum_address(token_addr), Web3.to_checksum_address(WBNB_ADDR)],
            account.address,
            int(time.time()) + 60
        ).build_transaction({
            "from": account.address, "gas": 300000,
            "gasPrice": w3.eth.gas_price,
            "nonce": w3.eth.get_transaction_count(account.address),
        })
        signed = account.sign_transaction(tx)
        txhash = w3.eth.send_raw_transaction(signed.raw_transaction)
        receipt = w3.eth.wait_for_transaction_receipt(txhash, timeout=60)
        if receipt.status == 1:
            bnb_after = w3.eth.get_balance(account.address)
            received = (bnb_after - bnb_before) / 1e18
            log(f"[✓] Продано → {received:.4f} BNB | tx: {txhash.hex()[:16]}...")
            return received
    except Exception as e:
        log(f"[ERR] sell_live: {e}")
    return None

def close_position(w3, router, account, pos, pair_addr, current_price, pct, reason,
                   positions, dry_run, tg_token, tg_chat, cfg, daily_loss=None):
    buy_bnb = pos["buy_amount_bnb"]
    symbol  = pos["symbol"]
    age     = time.time() - pos["buy_time"]

    sell_impact = 0.0
    g_sell      = 0.0
    total_cost  = buy_bnb

    if dry_run:
        g_sell     = _gas_dynamic(w3, cfg, "sim_gas_sell")
        total_cost = pos.get("total_cost_bnb", buy_bnb)
        tokens_raw = pos.get("token_amount", 0)
        sell_tax   = pos.get("sell_tax_pct", 0.0)
        bnb_r, tok_r = _get_reserves(w3, pair_addr, pos["is_token0_bnb"])
        if bnb_r and tok_r and tokens_raw:
            gross, n_br, n_tr = _amm_sell(bnb_r, tok_r, int(tokens_raw))
            sell_impact = _impact(bnb_r, tok_r, n_br, n_tr)
            # Применяем sell tax токена к выручке
            gross_after_tax = gross * (1 - sell_tax / 100)
            received = max(0.0, gross_after_tax - g_sell)
        else:
            received = buy_bnb * (1 + pct / 100) * (1 - sell_tax / 100) - g_sell
        pnl = received - total_cost
    else:
        received = sell_live(w3, router, account, pos["token_addr"], pos["token_amount"], cfg)
        if received is None:
            log(f"[WARN] продажа {symbol} не удалась, повтор через 5с")
            time.sleep(5)
            return
        pnl = received - buy_bnb

    if daily_loss is not None and pnl < 0:
        daily_loss[0] += pnl

    sign = "+" if pnl >= 0 else ""
    icon = "✓" if pnl >= 0 else "✗"
    pool_bnb = (pos.get("pool_bnb_at_entry", 0))
    sim_note = (f" | gas_s:{g_sell:.4f} imp:{sell_impact:.1f}%"
                f" cost:{total_cost:.4f}") if dry_run else ""
    log(f"[{icon}] {symbol} [{reason}]: цена {pct:+.1f}% | "
        f"P&L: {sign}{pnl:.4f} BNB | T+{age:.0f}с{sim_note}")

    history = load_history()
    history.append({
        "action":          "sell",
        "symbol":          symbol,
        "token":           pos["token_addr"],
        "bnb_spent":       buy_bnb,
        "gas_buy_bnb":     round(pos.get("gas_buy_bnb", 0), 6),
        "gas_sell_bnb":    round(g_sell, 6),
        "total_cost_bnb":  round(total_cost, 6),
        "bnb_received":    round(received, 6),
        "pnl_bnb":         round(pnl, 6),
        "price_pct":       round(pct, 2),
        "buy_impact_pct":  pos.get("price_impact_buy_pct", 0),
        "sell_impact_pct": round(sell_impact, 2),
        "latency_sec":     pos.get("buy_latency_sec", 0),
        "pool_bnb_entry":  pos.get("pool_bnb_at_entry", 0),
        "reason":          reason,
        "dev_bnb":         pos.get("dev_bnb"),
        "mev_pct":         pos.get("mev_pct", 0),
        "buy_tax_pct":     pos.get("buy_tax_pct", 0),
        "sell_tax_pct":    pos.get("sell_tax_pct", 0),
        "buy_time":        pos["buy_time"],
        "opened_at":       datetime.fromtimestamp(pos["buy_time"], tz=timezone.utc).isoformat(),
        "closed_at":       datetime.now(timezone.utc).isoformat(),
        "dry_run":         dry_run,
    })
    save_history(history)
    del positions[pair_addr]
    save_positions(positions)

    if tg_token and tg_chat:
        em = "✅" if pnl >= 0 else "❌"
        pfx = "[DRY] " if dry_run else ""
        tg_send(tg_token, tg_chat,
            f"{pfx}{em} <b>{symbol}</b>\n"
            f"P&amp;L: {sign}{pnl:.4f} BNB ({pct:+.1f}%)\n"
            f"Причина: {reason} | T+{age:.0f}с\n"
            f"Pool: {pos.get('pool_bnb_at_entry',0):.1f} BNB | "
            f"Imp buy/sell: {pos.get('price_impact_buy_pct',0):.1f}%/{sell_impact:.1f}%")

def main():
    global _log_file
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    _log_file = open(LOG_PATH, "a", encoding="utf-8")

    cfg = load_cfg()
    dry_run = cfg.get("dry_run", True)
    mode = "DRY-RUN" if dry_run else "LIVE"

    with open(KEYS_PATH, encoding="utf-8-sig") as f:
        keys = json.load(f)
    TG_TOKEN = keys.get("TELEGRAM_BOT_TOKEN", "")
    TG_CHAT  = str(keys.get("TELEGRAM_CHAT_ID", ""))

    log(f"=== BSC Sniper [{mode}] стартует ===")

    from web3 import Web3
    w3 = get_web3()
    log(f"Блок #{w3.eth.block_number}")

    account = None
    if not dry_run:
        wallet_key = keys.get("BSC_WALLET_KEY", "")
        if not wallet_key:
            log("[ERR] BSC_WALLET_KEY не задан в keys.json")
            sys.exit(1)
        account = w3.eth.account.from_key(wallet_key)
        log(f"[WALLET] {account.address}")

    factory = w3.eth.contract(address=Web3.to_checksum_address(FACTORY_ADDR), abi=FACTORY_ABI)
    router  = w3.eth.contract(address=Web3.to_checksum_address(ROUTER_ADDR),  abi=ROUTER_ABI)

    PAIR_CREATED_TOPIC = "0x" + w3.keccak(text="PairCreated(address,address,address,uint256)").hex()
    WBNB_lower = WBNB_ADDR.lower()

    pending   = {}  # pair_addr -> dict
    positions = load_positions()
    last_block = w3.eth.block_number - 1
    _last_heartbeat = [0.0]
    _daily_loss      = [0.0]   # убыток за текущий UTC-день
    _daily_loss_date = [datetime.now(timezone.utc).date()]

    if TG_TOKEN and TG_CHAT:
        tg_send(TG_TOKEN, TG_CHAT, f"🟡 BSC Sniper [{mode}] запущен\nЖдём новые токены...")

    log("Слушаем PancakeSwap Factory...")

    while True:
        cfg = load_cfg()
        # Heartbeat каждые 5 минут
        if time.time() - _last_heartbeat[0] >= 300:
            rpc_short = (_cur_rpc_url[0] or "?")[8:28]
            log(f"[♡] alive | блок {last_block} | RPC {rpc_short} | pending={len(pending)} pos={len(positions)}")
            _last_heartbeat[0] = time.time()

        # Сброс дневного счётчика убытка в 00:00 UTC
        _today = datetime.now(timezone.utc).date()
        if _today != _daily_loss_date[0]:
            _daily_loss[0] = 0.0
            _daily_loss_date[0] = _today
            log("[♻] Новые UTC-сутки — счётчик дневного убытка сброшен")

        # Дневной лимит убытка
        daily_limit = cfg.get("daily_loss_limit_bnb", 0)
        if daily_limit > 0 and _daily_loss[0] <= -daily_limit:
            log(f"[🛑] Дневной лимит убытка достигнут: {_daily_loss[0]:.4f} BNB"
                f" (лимит -{daily_limit} BNB). Пауза до 00:00 UTC.")
            if TG_TOKEN and TG_CHAT:
                tg_send(TG_TOKEN, TG_CHAT,
                    f"🛑 Дневной лимит убытка -{daily_limit} BNB достигнут!\n"
                    f"Бот на паузе до 00:00 UTC. Убыток сегодня: {_daily_loss[0]:.4f} BNB")
            time.sleep(60)
            continue

        now_h = datetime.now(timezone.utc).hour
        if now_h in cfg.get("pause_hours_utc", []):
            time.sleep(30)
            continue

        # ── Новые пары ────────────────────────────────────────────────────────
        try:
            cur = w3.eth.block_number
            if cur > last_block:
                to_block = min(cur, last_block + 5)
                logs = w3.eth.get_logs({
                    "fromBlock": hex(last_block + 1),
                    "toBlock":   hex(to_block),
                    "address":   Web3.to_checksum_address(FACTORY_ADDR),
                    "topics":    [PAIR_CREATED_TOPIC],
                })
                last_block = to_block

                for evt in logs:
                    try:
                        d = factory.events.PairCreated().process_log(evt)
                        t0 = d["args"]["token0"].lower()
                        t1 = d["args"]["token1"].lower()
                        pair_addr = d["args"]["pair"]

                        if t0 == WBNB_lower:
                            token_addr, is_token0_bnb = d["args"]["token1"], True
                        elif t1 == WBNB_lower:
                            token_addr, is_token0_bnb = d["args"]["token0"], False
                        else:
                            continue

                        # Начальная ликвидность
                        pair_c = w3.eth.contract(
                            address=Web3.to_checksum_address(pair_addr), abi=PAIR_ABI)
                        r = pair_c.functions.getReserves().call()
                        r0, r1 = r[0], r[1]
                        bnb_r = (r0 if is_token0_bnb else r1) / 1e18

                        if bnb_r < cfg.get("min_dev_buy_bnb", 1.0):
                            continue
                        if bnb_r > cfg.get("max_dev_buy_bnb", 1000.0):
                            continue  # данные: >50 BNB пулы дают лишь 8-16% win rate

                        price0 = get_price(w3, pair_addr, is_token0_bnb)
                        symbol = get_symbol(w3, token_addr)

                        log(f"[🎯] {symbol} | BNB: {bnb_r:.2f} | pair: {pair_addr[:12]}...")

                        pending[pair_addr] = {
                            "token_addr":    token_addr,
                            "pair_addr":     pair_addr,
                            "symbol":        symbol,
                            "is_token0_bnb": is_token0_bnb,
                            "initial_price": price0,
                            "detected_at":   time.time(),
                            "detected_block": evt["blockNumber"],
                            "dev_bnb":        bnb_r,
                        }
                    except Exception as e:
                        log(f"[ERR] evt: {e}")
        except Exception as e:
            err_str = str(e)
            log(f"[ERR] get_logs [{_cur_rpc_url[0] and _cur_rpc_url[0][8:28]}]: {e}")
            if any(x in err_str for x in ("32005", "32000", "32001", "32603", "limit exceeded", "block range", "403", "429", "Forbidden", "Too Many", "RemoteDisconnected", "Connection refused", "timed out", "EOF", "invalid block")):
                time.sleep(3)
                try:
                    w3 = get_web3(bad_url=_cur_rpc_url[0])
                except Exception:
                    time.sleep(10)
            else:
                time.sleep(5)
            continue

        now = time.time()

        # ── Входим в pending по второй волне ──────────────────────────────────
        to_remove = []
        for pair_addr, pt in list(pending.items()):
            age = now - pt["detected_at"]
            if age < cfg["entry_delay_sec"]:
                continue
            if pair_addr in positions or len(positions) >= cfg.get("max_concurrent", 1):
                if age > cfg["entry_delay_sec"] + 60:
                    to_remove.append(pair_addr)
                continue

            cur_price = get_price(w3, pair_addr, pt["is_token0_bnb"])
            if cur_price is None:
                to_remove.append(pair_addr)
                continue

            init_price = pt["initial_price"] or cur_price
            price_chg = (cur_price - init_price) / init_price * 100 if init_price else 0

            if price_chg < -cfg.get("entry_abandon_drop_pct", 50):
                log(f"[✗] {pt['symbol']} — rug при входе {price_chg:+.1f}%")
                to_remove.append(pair_addr)
                continue

            min_chg = cfg.get("entry_min_price_chg_pct", 0)
            if min_chg > 0 and price_chg < min_chg:
                log(f"[SKIP] {pt['symbol']} — нет импульса {price_chg:+.1f}% < {min_chg}% (мёртвый листинг)")
                to_remove.append(pair_addr)
                continue

            # ── Анализ Swap-событий: buy/sell давление ────────────────────────
            det_block = pt.get("detected_block", 0)
            if det_block:
                cur_blk = w3.eth.block_number
                buy_vol, sell_vol, buy_cnt, sell_cnt, max_sell = analyze_pair_swaps(
                    w3, pair_addr, pt["is_token0_bnb"], det_block, cur_blk)
                total_vol = buy_vol + sell_vol
                sell_pct  = sell_vol / total_vol * 100 if total_vol > 0.001 else 0.0

                # Доминирование продаж: >65% объёма — продажи
                max_sell_pct = cfg.get("max_sell_pressure_pct", 65)
                if sell_pct > max_sell_pct:
                    log(f"[SKIP] {pt['symbol']} — sell pressure {sell_pct:.0f}%"
                        f" (buy {buy_vol:.3f} vs sell {sell_vol:.3f} BNB,"
                        f" {buy_cnt}B/{sell_cnt}S)")
                    to_remove.append(pair_addr)
                    continue

                # Одиночный крупный сброс: один кошелёк продал >70% от суммарного buy vol
                if total_vol > 0.01 and buy_vol > 0 and max_sell > buy_vol * cfg.get("max_single_sell_ratio", 0.70):
                    log(f"[SKIP] {pt['symbol']} — whale/dev dump {max_sell:.3f} BNB"
                        f" (>{cfg.get('max_single_sell_ratio',0.70)*100:.0f}% от buy {buy_vol:.3f})")
                    to_remove.append(pair_addr)
                    continue

                pt["_swap_note"] = (f"B{buy_cnt}/{sell_cnt}S"
                                    f" vol={total_vol:.3f}BNB sell%={sell_pct:.0f}%")

            # ── LP стабильность: BNB-резервы не убежали ───────────────────────
            bnb_r_now, _ = _get_reserves(w3, pair_addr, pt["is_token0_bnb"])
            if bnb_r_now:
                cur_pool = bnb_r_now / 1e18
                lp_drop  = (pt["dev_bnb"] - cur_pool) / pt["dev_bnb"] * 100
                if lp_drop > cfg.get("max_lp_drop_pct", 35):
                    log(f"[SKIP] {pt['symbol']} — LP упала {lp_drop:.0f}%"
                        f" ({pt['dev_bnb']:.1f}→{cur_pool:.1f} BNB, LP удаляется?)")
                    to_remove.append(pair_addr)
                    continue

            swap_note = pt.get("_swap_note", "")
            log(f"[→] {pt['symbol']} | Δ цена: {price_chg:+.1f}% за {age:.0f}с"
                f" | dev: {pt['dev_bnb']:.2f} BNB | {swap_note}")

            buy_bnb = cfg["buy_amount_bnb"]

            if dry_run:
                # ── Реалистичная симуляция: AMM + tax + MEV + gas + latency ──
                bnb_r, tok_r = _get_reserves(w3, pair_addr, pt["is_token0_bnb"])
                if bnb_r is None:
                    log(f"[SKIP] {pt['symbol']} — нет резервов")
                    to_remove.append(pair_addr)
                    continue

                tokens_raw, new_bnb_r, new_tok_r = _amm_buy(bnb_r, tok_r, buy_bnb)
                if tokens_raw == 0:
                    log(f"[SKIP] {pt['symbol']} — AMM 0 токенов (слишком мало ликвидности)")
                    to_remove.append(pair_addr)
                    continue

                buy_impact = _impact(bnb_r, tok_r, new_bnb_r, new_tok_r)
                max_imp = cfg.get("max_price_impact_pct", 15)
                if buy_impact > max_imp:
                    log(f"[SKIP] {pt['symbol']} — impact {buy_impact:.1f}% > {max_imp}% лимит")
                    to_remove.append(pair_addr)
                    continue

                # Supply + bytecode safety (до honeypot — быстрее отсеивает мусор)
                safe, safety_note = check_contract_safety(w3, pt["token_addr"], tok_r)
                if not safe:
                    log(f"[SKIP] {pt['symbol']} — {safety_note}")
                    to_remove.append(pair_addr)
                    continue
                if safety_note:
                    log(f"[WARN] {pt['symbol']} — {safety_note}")

                # GoPlus Security API (30+ on-chain checks)
                gp_ok, gp_note = check_goplus(pt["token_addr"])
                if not gp_ok:
                    log(f"[SKIP] {pt['symbol']} — {gp_note}")
                    to_remove.append(pair_addr)
                    continue
                log(f"[CHECK] {pt['symbol']} GoPlus: {gp_note}")

                # honeypot.is — симуляция buy+sell через API
                hp_ok, hp_note = check_honeypot_is(pt["token_addr"])
                if not hp_ok:
                    log(f"[SKIP] {pt['symbol']} — {hp_note}")
                    to_remove.append(pair_addr)
                    continue
                log(f"[CHECK] {pt['symbol']} honeypot.is: {hp_note}")

                # Honeypot + налог
                tok_ok, buy_tax, sell_tax = check_token(w3, router, pt["token_addr"])
                if not tok_ok:
                    log(f"[SKIP] {pt['symbol']} — honeypot (eth_call revert)")
                    to_remove.append(pair_addr)
                    continue
                roundtrip_tax = buy_tax + sell_tax
                if roundtrip_tax > cfg.get("max_token_tax_pct", _MAX_ROUNDTRIP_TAX_PCT):
                    log(f"[SKIP] {pt['symbol']} — налог {roundtrip_tax:.1f}% > лимит")
                    to_remove.append(pair_addr)
                    continue

                # MEV сэндвич: уменьшает реально полученные токены
                mev = _mev_penalty()
                tokens_after_tax = int(tokens_raw * (1 - buy_tax / 100) * (1 - mev))

                g_buy      = _gas_dynamic(w3, cfg, "sim_gas_buy")
                latency    = _latency(cfg)
                total_cost = buy_bnb + g_buy
                pool_bnb   = bnb_r / 1e18

                positions[pair_addr] = {
                    "token_addr":          pt["token_addr"],
                    "pair_addr":           pair_addr,
                    "symbol":              pt["symbol"],
                    "is_token0_bnb":       pt["is_token0_bnb"],
                    "buy_amount_bnb":      buy_bnb,
                    "gas_buy_bnb":         round(g_buy, 6),
                    "total_cost_bnb":      round(total_cost, 6),
                    "token_amount":        tokens_after_tax,
                    "buy_tax_pct":         round(buy_tax, 2),
                    "sell_tax_pct":        round(sell_tax, 2),
                    "mev_pct":             round(mev * 100, 2),
                    "entry_price":         cur_price,
                    "peak_price":          cur_price,
                    "buy_time":            now,
                    "buy_latency_sec":     round(latency, 1),
                    "price_impact_buy_pct": round(buy_impact, 2),
                    "pool_bnb_at_entry":   round(pool_bnb, 2),
                    "dev_bnb":             pt["dev_bnb"],
                    "dry_run":             True,
                }
                tax_note = f" tax={roundtrip_tax:.0f}%" if roundtrip_tax > 0 else ""
                log(f"[DRY] {pt['symbol']} | {buy_bnb}+{g_buy:.4f}gas BNB"
                    f" | impact {buy_impact:.1f}% MEV {mev*100:.1f}%{tax_note}"
                    f" | pool {pool_bnb:.1f} BNB | latency ~{latency:.1f}с")
                h = load_history()
                h.append({
                    "action":               "buy",
                    "symbol":               pt["symbol"],
                    "token":                pt["token_addr"],
                    "bnb_spent":            buy_bnb,
                    "gas_buy_bnb":          round(g_buy, 6),
                    "total_cost_bnb":       round(total_cost, 6),
                    "dev_bnb":              pt["dev_bnb"],
                    "pool_bnb_at_entry":    round(pool_bnb, 2),
                    "entry_delay_sec":      round(age, 1),
                    "price_chg_at_entry_pct": round(price_chg, 2),
                    "buy_impact_pct":       round(buy_impact, 2),
                    "buy_tax_pct":          round(buy_tax, 2),
                    "sell_tax_pct":         round(sell_tax, 2),
                    "mev_pct":              round(mev * 100, 2),
                    "latency_sec":          round(latency, 1),
                    "timestamp":            datetime.now(timezone.utc).isoformat(),
                    "dry_run":              True,
                })
                save_history(h)
                save_positions(positions)
                if TG_TOKEN and TG_CHAT:
                    tg_send(TG_TOKEN, TG_CHAT,
                        f"[DRY] 🟢 Купил <b>{pt['symbol']}</b>\n"
                        f"Pool: {pool_bnb:.1f} BNB | Impact: {buy_impact:.1f}%\n"
                        f"Gas: {g_buy:.4f} BNB | Latency: ~{latency:.1f}с\n"
                        f"Вход через {age:.0f}с | Δ цена: {price_chg:+.1f}%")
            else:
                tok = buy_live(w3, router, account, pt["token_addr"], buy_bnb, cfg)
                if tok:
                    positions[pair_addr] = {
                        "token_addr": pt["token_addr"],
                        "pair_addr":  pair_addr,
                        "symbol":     pt["symbol"],
                        "is_token0_bnb": pt["is_token0_bnb"],
                        "buy_amount_bnb": buy_bnb,
                        "token_amount":   tok,
                        "entry_price":    cur_price,
                        "peak_price":     cur_price,
                        "buy_time":       now,
                        "dev_bnb":         pt["dev_bnb"],
                        "dry_run":         False,
                    }
                    save_positions(positions)

            to_remove.append(pair_addr)

        for k in to_remove:
            pending.pop(k, None)

        # ── Управление позициями ──────────────────────────────────────────────
        for pair_addr, pos in list(positions.items()):
            try:
                cur_price = get_price(w3, pos["pair_addr"], pos["is_token0_bnb"])
                if cur_price is None:
                    age = now - pos["buy_time"]
                    null_count = pos.get("_null_count", 0) + 1
                    pos["_null_count"] = null_count
                    positions[pair_addr] = pos
                    if null_count >= 5 or age >= cfg["position_timeout_sec"]:
                        log(f"[✗] {pos['symbol']} — цена недоступна (rug?), закрываем")
                        close_position(w3, router, account, pos, pair_addr,
                                       pos["entry_price"], -100, "rug_no_price",
                                       positions, dry_run, TG_TOKEN, TG_CHAT, cfg,
                                       daily_loss=_daily_loss)
                    continue

                entry = pos["entry_price"]
                pct   = (cur_price - entry) / entry * 100
                age   = now - pos["buy_time"]

                # LP drain: дев вывел ликвидность после нашего входа → экстренное закрытие
                _ep = pos.get("pool_bnb_at_entry", 0)
                if _ep > 0:
                    _bnb_r, _ = _get_reserves(w3, pos["pair_addr"], pos["is_token0_bnb"])
                    if _bnb_r is not None:
                        _cur_pool = _bnb_r / 1e18
                        _drain = (_ep - _cur_pool) / _ep * 100
                        if _drain > cfg.get("lp_drain_close_pct", 50):
                            log(f"[!] {pos['symbol']} — LP drain {_drain:.0f}%"
                                f" ({_ep:.1f}→{_cur_pool:.1f} BNB), экстренное закрытие")
                            close_position(w3, router, account, pos, pair_addr, cur_price, pct,
                                           "lp_drain", positions, dry_run, TG_TOKEN, TG_CHAT, cfg,
                                           daily_loss=_daily_loss)
                            continue

                if cur_price > pos.get("peak_price", entry):
                    pos["peak_price"] = cur_price
                    positions[pair_addr] = pos

                peak_pct      = (pos["peak_price"] - entry) / entry * 100
                drop_from_peak = (cur_price - pos["peak_price"]) / pos["peak_price"] * 100

                reason = None

                # Breakeven lock: если пик достиг порога — выходим не ниже входа
                # Приоритет над всеми стопами кроме early_stop (rug-защита)
                _be_act = cfg.get("breakeven_activate_pct", 0)
                _be_buf = cfg.get("breakeven_buffer_pct", 2.0)
                if _be_act > 0 and peak_pct >= _be_act and pct < -_be_buf:
                    reason = "breakeven"

                if not reason:
                    if age >= cfg["stoploss_early_delay_sec"] and pct <= -cfg["stoploss_early_loss_pct"]:
                        reason = "early_stop"
                    elif age >= cfg["stoploss2_delay_sec"] and pct <= -cfg["stoploss2_loss_pct"]:
                        reason = "stoploss2"
                    elif age >= cfg["stoploss3_delay_sec"] and pct <= -cfg["stoploss3_loss_pct"]:
                        reason = "stoploss3"
                    elif (peak_pct >= cfg["trailing_stop_activate_pct"] and
                          drop_from_peak <= -cfg["trailing_stop_pct"]):
                        reason = "trailing_stop"
                    elif age >= cfg["position_timeout_sec"]:
                        if pct >= cfg.get("timeout_extend_win_pct", 15) and age < cfg.get("timeout_max_sec", 180):
                            pass
                        else:
                            reason = "timeout"

                if reason:
                    close_position(w3, router, account, pos, pair_addr, cur_price, pct, reason,
                                   positions, dry_run, TG_TOKEN, TG_CHAT, cfg,
                                   daily_loss=_daily_loss)

            except Exception as e:
                log(f"[ERR] pos {pair_addr[:12]}: {e}")

        time.sleep(3)


if __name__ == "__main__":
    main()
