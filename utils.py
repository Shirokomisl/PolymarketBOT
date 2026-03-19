# utils.py
import math
from typing import Optional, Tuple

from config import Config


def compute_probability(open_price: float, high: float, low: float, close: float, scale: float) -> float:
    vol = max(high - low, 1e-9)
    move = abs(close - open_price)
    ratio = min(move / vol, 1.0)
    return 0.5 + 0.5 * math.tanh(ratio * scale)


def resolve_direction(open_price: float, close: float) -> str:
    return "UP" if close >= open_price else "DOWN"


def predict_direction(
    price_history: list, 
    volume_history: list, 
    config: Config
) -> Tuple[Optional[str], float]:
    """
    Прогнозирует направление движения BTC.
    Адаптировано под реалии BTC: 0.5% изменение = сильный сигнал
    """
    if len(price_history) < 2:
        return None, 0.0
    
    current_price = price_history[-1]['close']
    old_price = price_history[0]['close']
    
    # 1. Скорость изменения (ROC) - для BTC 0.5% = полная уверенность
    price_change_pct = abs((current_price - old_price) / old_price * 100)
    roc_score = min(price_change_pct / 0.5, 1.0)  # 0.5% изменение = 100% уверенности
    
    # 2. Направление
    if current_price > old_price:
        direction = "UP"
    elif current_price < old_price:
        direction = "DOWN"
    else:
        return None, 0.0
    
    # 3. Импульс (сравниваем с предыдущим периодом)
    momentum_score = 0.0
    if len(price_history) >= 3:
        prev_price = price_history[-2]['close']
        prev_price2 = price_history[-3]['close']
        
        prev_change = abs(prev_price - prev_price2) / prev_price2 * 100
        current_change = abs(current_price - prev_price) / prev_price * 100
        
        if current_change > prev_change * 1.2:  # ускорение на 20%
            momentum_score = 0.2
        elif current_change < prev_change * 0.8:  # замедление на 20%
            momentum_score = -0.2
    
    # 4. Подтверждение объёмом (для BTC нормальный объём ~20-30 BTC за 5 мин)
    volume_score = 0.0
    if len(volume_history) >= 3:
        avg_volume = sum(volume_history[:-1]) / (len(volume_history) - 1)
        current_volume = volume_history[-1]
        volume_ratio = current_volume / avg_volume if avg_volume > 0 else 1.0
        
        if volume_ratio >= 2.0:  # объём в 2 раза выше среднего
            volume_score = 0.3
        elif volume_ratio >= 1.5:  # в 1.5 раза выше
            volume_score = 0.15
    
    # Считаем уверенность
    confidence = roc_score + momentum_score + volume_score
    confidence = max(0.0, min(confidence, 1.0))
    
    # Для отладки
    if confidence > 0.5:
        print(f"DEBUG: change={price_change_pct:.3f}%, roc={roc_score:.2f}, "
              f"momentum={momentum_score:.2f}, volume={volume_score:.2f}, "
              f"conf={confidence:.2f}")
    
    return direction, confidence