from fastapi import FastAPI, HTTPException, Depends
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse
from sqlalchemy import create_engine, Column, Integer, String, Float, DateTime
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, Session
from pydantic import BaseModel
from datetime import datetime
from typing import List, Optional
import os

# 数据库配置
DATABASE_URL = "sqlite:///./money_calculator.db"
engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

# 数据库模型
class CalculationHistory(Base):
    __tablename__ = "calculation_history"
    
    id = Column(Integer, primary_key=True, index=True)
    initial_total = Column(Float, nullable=False)
    my_initial = Column(Float, nullable=False)
    my_charge = Column(Float, nullable=False)
    final_total = Column(Float, nullable=False)
    others_money = Column(Float, nullable=False)
    my_remaining = Column(Float, nullable=False)
    my_consumed = Column(Float, nullable=False)
    note = Column(String, default="")
    created_at = Column(DateTime, default=datetime.utcnow)

class SavedSettings(Base):
    __tablename__ = "saved_settings"
    
    id = Column(Integer, primary_key=True, index=True)
    key = Column(String, unique=True, nullable=False)
    value = Column(Float, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

# 创建数据库表
Base.metadata.create_all(bind=engine)

# Pydantic模型
class CardCalculation(BaseModel):
    initial_total: float
    my_initial: float
    my_charge: float
    final_total: float
    note: Optional[str] = ""

class CalculationResult(BaseModel):
    id: Optional[int]
    others_money: float
    my_remaining: float
    my_consumed: float
    calculation_steps: list
    created_at: Optional[datetime]

class HistoryResponse(BaseModel):
    id: int
    initial_total: float
    my_initial: float
    my_charge: float
    final_total: float
    others_money: float
    my_remaining: float
    my_consumed: float
    note: str
    created_at: datetime
    
    class Config:
        from_attributes = True

class QuickInputData(BaseModel):
    others_money: float
    my_current_balance: float

# FastAPI应用
app = FastAPI(title="饭卡分账助手", description="计算共享饭卡中个人剩余金额")

# 静态文件
app.mount("/static", StaticFiles(directory="static"), name="static")

# 数据库依赖
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

# 获取或设置默认值
def get_saved_value(db: Session, key: str, default: float = 0.0) -> float:
    setting = db.query(SavedSettings).filter(SavedSettings.key == key).first()
    return setting.value if setting else default

def save_value(db: Session, key: str, value: float):
    setting = db.query(SavedSettings).filter(SavedSettings.key == key).first()
    if setting:
        setting.value = value
        setting.updated_at = datetime.utcnow()
    else:
        setting = SavedSettings(key=key, value=value)
        db.add(setting)
    db.commit()

# 首页
@app.get("/", response_class=HTMLResponse)
def read_root():
    return HTMLResponse(open("static/index.html", "r", encoding="utf-8").read())

# 获取默认值
@app.get("/api/defaults/")
def get_defaults(db: Session = Depends(get_db)):
    # 从最后一次计算中获取合理的默认值
    latest = db.query(CalculationHistory).order_by(CalculationHistory.created_at.desc()).first()
    if latest:
        return {
            "others_money": latest.others_money,
            "my_remaining": latest.my_remaining
        }
    return {"others_money": 7.0, "my_remaining": 0.0}

# 计算饭卡金额并保存
@app.post("/api/calculate/", response_model=CalculationResult)
def calculate_money(data: CardCalculation, db: Session = Depends(get_db)):
    # 验证输入
    if data.initial_total < 0 or data.my_initial < 0 or data.my_charge < 0 or data.final_total < 0:
        raise HTTPException(status_code=400, detail="所有金额都必须大于等于0")
    
    if data.my_initial > data.initial_total:
        raise HTTPException(status_code=400, detail="你的初始金额不能超过饭卡总额")
    
    # 计算步骤
    steps = []
    
    # 1. 计算别人的金额（固定不变）
    others_money = data.initial_total - data.my_initial
    steps.append(f"别人的金额 = 饭卡初始总额 - 你的初始金额 = {data.initial_total} - {data.my_initial} = {others_money}")
    
    # 2. 计算充值后你的金额
    my_after_charge = data.my_initial + data.my_charge
    steps.append(f"你充值后的金额 = 你的初始金额 + 充值金额 = {data.my_initial} + {data.my_charge} = {my_after_charge}")
    
    # 3. 计算充值后饭卡总额
    total_after_charge = data.initial_total + data.my_charge
    steps.append(f"充值后饭卡总额 = 初始总额 + 充值金额 = {data.initial_total} + {data.my_charge} = {total_after_charge}")
    
    # 4. 计算你剩余的金额
    my_remaining = data.final_total - others_money
    steps.append(f"你剩余的金额 = 打饭后总余额 - 别人的金额 = {data.final_total} - {others_money} = {my_remaining}")
    
    # 5. 计算你消费的金额
    my_consumed = my_after_charge - my_remaining
    steps.append(f"你消费的金额 = 充值后你的金额 - 你剩余的金额 = {my_after_charge} - {my_remaining} = {my_consumed}")
    
    # 验证计算结果
    if my_remaining < 0:
        raise HTTPException(status_code=400, detail="计算结果显示你的余额为负数，请检查输入数据")
    
    if my_consumed < 0:
        raise HTTPException(status_code=400, detail="计算结果显示你的消费为负数，请检查输入数据")
    
    # 保存到数据库
    history_record = CalculationHistory(
        initial_total=data.initial_total,
        my_initial=data.my_initial,
        my_charge=data.my_charge,
        final_total=data.final_total,
        others_money=others_money,
        my_remaining=my_remaining,
        my_consumed=my_consumed,
        note=data.note or ""
    )
    db.add(history_record)
    db.commit()
    db.refresh(history_record)
    
    return CalculationResult(
        id=history_record.id,
        others_money=others_money,
        my_remaining=my_remaining,
        my_consumed=my_consumed,
        calculation_steps=steps,
        created_at=history_record.created_at
    )

# 快速输入计算（基于上次余额）
@app.post("/api/quick_calculate/")
def quick_calculate(
    charge_amount: float,
    final_total: float,
    note: str = "",
    db: Session = Depends(get_db)
):
    # 获取最后一次的计算结果
    latest = db.query(CalculationHistory).order_by(CalculationHistory.created_at.desc()).first()
    
    if not latest:
        raise HTTPException(status_code=400, detail="没有找到历史数据，请使用完整计算")
    
    # 使用上次的别人金额和我的余额作为基础
    others_money = latest.others_money
    my_current_balance = latest.my_remaining
    
    # 计算新的数据
    my_after_charge = my_current_balance + charge_amount
    initial_total = others_money + my_current_balance
    new_initial_total = initial_total + charge_amount
    
    # 构造计算数据
    calc_data = CardCalculation(
        initial_total=new_initial_total,
        my_initial=my_current_balance,
        my_charge=charge_amount,
        final_total=final_total,
        note=note
    )
    
    return calculate_money(calc_data, db)

# 获取历史记录
@app.get("/api/history/", response_model=List[HistoryResponse])
def get_history(limit: int = 20, db: Session = Depends(get_db)):
    records = db.query(CalculationHistory).order_by(
        CalculationHistory.created_at.desc()
    ).limit(limit).all()
    return records

# 删除历史记录
@app.delete("/api/history/{record_id}")
def delete_history(record_id: int, db: Session = Depends(get_db)):
    record = db.query(CalculationHistory).filter(CalculationHistory.id == record_id).first()
    if not record:
        raise HTTPException(status_code=404, detail="记录不存在")
    
    db.delete(record)
    db.commit()
    return {"message": "记录已删除"}

# 清除所有历史记录
@app.delete("/api/history/")
def clear_all_history(db: Session = Depends(get_db)):
    db.query(CalculationHistory).delete()
    db.query(SavedSettings).delete()
    db.commit()
    return {"message": "所有数据已清除"}

# 获取最新状态
@app.get("/api/latest_status/")
def get_latest_status(db: Session = Depends(get_db)):
    latest = db.query(CalculationHistory).order_by(CalculationHistory.created_at.desc()).first()
    if not latest:
        return {"has_data": False}
    
    return {
        "has_data": True,
        "others_money": latest.others_money,
        "my_remaining": latest.my_remaining,
        "last_calculation": latest.created_at.strftime("%Y-%m-%d %H:%M")
    }

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=4544)