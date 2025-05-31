from fastapi import FastAPI, HTTPException, Depends, status, Body
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from motor.motor_asyncio import AsyncIOMotorClient
from pydantic import BaseModel, Field
from typing import Optional, List
from datetime import datetime, timedelta
from bson import ObjectId
import hashlib
import os
import uvicorn
from dotenv import load_dotenv
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from io import BytesIO
from reportlab.pdfgen import canvas

load_dotenv()

# MongoDB connection
MONGODB_URL = os.getenv("MONGODB_URL", "mongodb://localhost:27017")
DATABASE_NAME = "provision_store"

app = FastAPI(title="Smart Inventory & Sales Tracking", version="1.0.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Or specify your frontend URL
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
security = HTTPBearer()

# Database client
client = AsyncIOMotorClient(MONGODB_URL)
db = client[DATABASE_NAME]

# Collections
products_collection = db.products
sales_collection = db.sales
bottles_collection = db.bottles
settings_collection = db.settings
undo_log_collection = db.undo_log

# Pydantic Models
class Product(BaseModel):
    name: str
    unit_price: float
    quantity: int
    is_bottled: bool = False
    barcode: Optional[str] = None  # <-- Add this line

class ProductResponse(BaseModel):
    id: str
    name: str
    unit_price: float
    quantity: int
    is_bottled: bool
    barcode: Optional[str] = None  # <-- Add this line
    low_stock: bool = False

class ProductUpdate(BaseModel):
    name: Optional[str] = None
    unit_price: Optional[float] = None
    quantity: Optional[int] = None
    is_bottled: Optional[bool] = None
    barcode: Optional[str] = None  # <-- Add this line

class Sale(BaseModel):
    product_id: str
    quantity: int
    bottle_taken: bool = False
    sold_by: str
    payment_method: str = Field(..., pattern="^(Cash|POS|Transfer|Credit)$")
    customer_id: Optional[str] = None  # <-- Add this

class SaleResponse(BaseModel):
    id: str
    product_id: str
    product_name: str
    quantity: int
    unit_price: float
    total_amount: float
    bottle_taken: bool
    sold_by: str
    payment_method: str
    timestamp: datetime

class SaleItem(BaseModel):
    product_id: str
    quantity: int
    bottle_taken: bool = False

class MultiSale(BaseModel):
    items: List[SaleItem]
    sold_by: str
    payment_method: str = Field(..., pattern="^(Cash|POS|Transfer|Credit)$")
    customer_id: Optional[str] = None  # <-- Add this

class MultiSaleResponse(BaseModel):
    id: str
    items: List[dict]
    sold_by: str
    payment_method: str
    total_amount: float
    timestamp: datetime

class BottleReturn(BaseModel):
    product_name: str
    bottles_returned: int

class BottleReturnWithCustomer(BaseModel):
    product_name: str
    bottles_returned: int
    customer_name: str

class PinAuth(BaseModel):
    pin: str

class DailySummary(BaseModel):
    date: str
    total_sales_amount: float
    sales_by_seller: dict
    bottles_taken: int
    bottles_returned: int
    outstanding_bottles: int
    low_stock_products: List[ProductResponse]

class Customer(BaseModel):
    name: str
    phone: Optional[str] = None
    email: Optional[str] = None

class CustomerResponse(BaseModel):
    id: str
    name: str
    phone: Optional[str] = None
    email: Optional[str] = None

# Helper functions
def hash_pin(pin: str) -> str:
    return hashlib.sha256(pin.encode()).hexdigest()

def product_dict_to_response(product_dict: dict) -> ProductResponse:
    low_stock_threshold = 5  # Default threshold
    return ProductResponse(
        id=str(product_dict["_id"]),
        name=product_dict["name"],
        unit_price=product_dict["unit_price"],
        quantity=product_dict["quantity"],
        is_bottled=product_dict["is_bottled"],
        low_stock=product_dict["quantity"] < low_stock_threshold
    )

async def verify_pin(credentials: HTTPAuthorizationCredentials = Depends(security)):
    settings = await settings_collection.find_one({})
    if not settings or "pin" not in settings:
        # Initialize default PIN if not set
        default_pin_hash = hash_pin("1234")
        await settings_collection.insert_one({"pin": default_pin_hash, "low_stock_threshold": 5})
        stored_pin_hash = default_pin_hash
    else:
        stored_pin_hash = settings["pin"]
    
    provided_pin_hash = hash_pin(credentials.credentials)
    if provided_pin_hash != stored_pin_hash:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid PIN"
        )
    return True

# Authentication endpoints
@app.post("/auth/login")
async def login(auth: PinAuth = Body(...)):
    settings = await settings_collection.find_one({})
    if not settings or "pin" not in settings:
        # Initialize default PIN if not set
        default_pin_hash = hash_pin("1234")
        await settings_collection.insert_one({"pin": default_pin_hash, "low_stock_threshold": 5})
        stored_pin_hash = default_pin_hash
    else:
        stored_pin_hash = settings["pin"]
    
    provided_pin_hash = hash_pin(auth.pin)
    if provided_pin_hash != stored_pin_hash:
        raise HTTPException(status_code=401, detail="Invalid PIN")
    
    return {"message": "Authentication successful", "token": auth.pin}

@app.post("/auth/change-pin")
async def change_pin(old_pin: str, new_pin: str, _: bool = Depends(verify_pin)):
    if len(new_pin) != 4 or not new_pin.isdigit():
        raise HTTPException(status_code=400, detail="PIN must be 4 digits")
    
    new_pin_hash = hash_pin(new_pin)
    await settings_collection.update_one(
        {},
        {"$set": {"pin": new_pin_hash}},
        upsert=True
    )
    return {"message": "PIN changed successfully"}

# Product endpoints
@app.post("/products", response_model=ProductResponse)
async def add_product(product: Product, _: bool = Depends(verify_pin)):
    # Check if product already exists
    existing = await products_collection.find_one({"name": product.name})
    if existing:
        raise HTTPException(status_code=400, detail="Product already exists")
    
    product_dict = product.dict()
    result = await products_collection.insert_one(product_dict)
    
    # Initialize bottle tracking if product is bottled
    if product.is_bottled:
        await bottles_collection.insert_one({
            "product_id": str(result.inserted_id),
            "bottles_taken": 0,
            "bottles_returned": 0
        })
    
    product_dict["_id"] = result.inserted_id
    return product_dict_to_response(product_dict)

@app.get("/products", response_model=List[ProductResponse])
async def get_products(_: bool = Depends(verify_pin)):
    products = []
    async for product in products_collection.find():
        products.append(product_dict_to_response(product))
    return products

@app.get("/products/{product_id}", response_model=ProductResponse)
async def get_product(product_id: str, _: bool = Depends(verify_pin)):
    try:
        product = await products_collection.find_one({"_id": ObjectId(product_id)})
    except:
        raise HTTPException(status_code=400, detail="Invalid product ID")
    
    if not product:
        raise HTTPException(status_code=404, detail="Product not found")
    
    return product_dict_to_response(product)

@app.put("/products/{product_id}", response_model=ProductResponse)
async def update_product(product_id: str, product_update: ProductUpdate, _: bool = Depends(verify_pin)):
    try:
        object_id = ObjectId(product_id)
    except:
        raise HTTPException(status_code=400, detail="Invalid product ID")
    
    update_data = {k: v for k, v in product_update.dict().items() if v is not None}
    if not update_data:
        raise HTTPException(status_code=400, detail="No data to update")
    
    result = await products_collection.update_one(
        {"_id": object_id},
        {"$set": update_data}
    )
    
    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail="Product not found")
    
    updated_product = await products_collection.find_one({"_id": object_id})
    return product_dict_to_response(updated_product)

@app.delete("/products/{product_id}")
async def delete_product(product_id: str, _: bool = Depends(verify_pin)):
    try:
        object_id = ObjectId(product_id)
    except:
        raise HTTPException(status_code=400, detail="Invalid product ID")
    
    result = await products_collection.delete_one({"_id": object_id})
    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Product not found")
    
    # Clean up bottle tracking
    await bottles_collection.delete_one({"product_id": product_id})
    
    return {"message": "Product deleted successfully"}

# Sales endpoints
@app.post("/sales", response_model=SaleResponse)
async def record_sale(sale: Sale, _: bool = Depends(verify_pin)):
    try:
        product_id = ObjectId(sale.product_id)
    except:
        raise HTTPException(status_code=400, detail="Invalid product ID")
    
    # Get product details
    product = await products_collection.find_one({"_id": product_id})
    if not product:
        raise HTTPException(status_code=404, detail="Product not found")
    
    # Check stock availability
    if product["quantity"] < sale.quantity:
        raise HTTPException(status_code=400, detail="Insufficient stock")
    
    # Calculate total amount
    total_amount = product["unit_price"] * sale.quantity
    
    # Create sale record
    sale_record = {
        "product_id": sale.product_id,
        "product_name": product["name"],
        "quantity": sale.quantity,
        "unit_price": product["unit_price"],
        "total_amount": total_amount,
        "bottle_taken": sale.bottle_taken if product["is_bottled"] else False,
        "sold_by": sale.sold_by,
        "payment_method": sale.payment_method,
        "timestamp": datetime.now()
    }
    
    # Insert sale record
    sale_result = await sales_collection.insert_one(sale_record)
    
    # Update product quantity
    await products_collection.update_one(
        {"_id": product_id},
        {"$inc": {"quantity": -sale.quantity}}
    )
    
    # Update bottle tracking if applicable
    if product["is_bottled"] and sale.bottle_taken:
        await bottles_collection.update_one(
            {"product_id": sale.product_id},
            {"$inc": {"bottles_taken": sale.quantity}},
            upsert=True
        )
    
    # Add to undo log
    await undo_log_collection.insert_one({
        "sale_id": str(sale_result.inserted_id),
        "timestamp": datetime.now()
    })
    
    # Clean old undo logs (older than 5 minutes)
    five_minutes_ago = datetime.now() - timedelta(minutes=5)
    await undo_log_collection.delete_many({"timestamp": {"$lt": five_minutes_ago}})
    
    sale_record["id"] = str(sale_result.inserted_id)
    return SaleResponse(**sale_record)

@app.post("/sales/multi", response_model=MultiSaleResponse)
async def record_multi_sale(sale: MultiSale, _: bool = Depends(verify_pin)):
    total_amount = 0
    sale_items = []
    for item in sale.items:
        try:
            product_id = ObjectId(item.product_id)
        except:
            raise HTTPException(status_code=400, detail="Invalid product ID")
        product = await products_collection.find_one({"_id": product_id})
        if not product:
            raise HTTPException(status_code=404, detail=f"Product not found: {item.product_id}")
        if product["quantity"] < item.quantity:
            raise HTTPException(status_code=400, detail=f"Insufficient stock for {product['name']}")
        amount = product["unit_price"] * item.quantity
        total_amount += amount
        sale_items.append({
            "product_id": item.product_id,
            "product_name": product["name"],
            "quantity": item.quantity,
            "unit_price": product["unit_price"],
            "total_amount": amount,
            "bottle_taken": item.bottle_taken if product["is_bottled"] else False
        })
        # Update product quantity
        await products_collection.update_one(
            {"_id": product_id},
            {"$inc": {"quantity": -item.quantity}}
        )
        # Update bottle tracking if applicable
        if product["is_bottled"] and item.bottle_taken:
            await bottles_collection.update_one(
                {"product_id": item.product_id},
                {"$inc": {"bottles_taken": item.quantity}},
                upsert=True
            )
    sale_record = {
        "items": sale_items,
        "sold_by": sale.sold_by,
        "payment_method": sale.payment_method,
        "total_amount": total_amount,
        "timestamp": datetime.now()
    }
    sale_result = await sales_collection.insert_one(sale_record)
    sale_record["id"] = str(sale_result.inserted_id)
    return MultiSaleResponse(**sale_record)

@app.get("/sales", response_model=List[SaleResponse])
async def get_sales(
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    product_name: Optional[str] = None,
    seller_name: Optional[str] = None,
    _: bool = Depends(verify_pin)
):
    filter_dict = {}

    # Date range filter
    if start_date or end_date:
        date_filter = {}
        if start_date:
            try:
                date_filter["$gte"] = datetime.fromisoformat(start_date)
            except:
                raise HTTPException(status_code=400, detail="Invalid start_date format")
        if end_date:
            try:
                date_filter["$lte"] = datetime.fromisoformat(end_date)
            except:
                raise HTTPException(status_code=400, detail="Invalid end_date format")
        filter_dict["timestamp"] = date_filter

    # Product name filter
    if product_name:
        filter_dict["product_name"] = {"$regex": product_name, "$options": "i"}

    # Seller name filter
    if seller_name:
        filter_dict["sold_by"] = {"$regex": seller_name, "$options": "i"}

    sales = []
    async for sale in sales_collection.find(filter_dict).sort("timestamp", -1):
        if "items" in sale:
            # Multi-sale: flatten each item
            for item in sale["items"]:
                sales.append(SaleResponse(
                    id=str(sale["_id"]),
                    product_id=item["product_id"],
                    product_name=item["product_name"],
                    quantity=item["quantity"],
                    unit_price=item["unit_price"],
                    total_amount=item["total_amount"],
                    bottle_taken=item.get("bottle_taken", False),
                    sold_by=sale["sold_by"],
                    payment_method=sale["payment_method"],
                    timestamp=sale["timestamp"]
                ))
        else:
            sale["id"] = str(sale["_id"])
            sales.append(SaleResponse(**sale))
    return sales

@app.delete("/sales/undo-last")
async def undo_last_sale(_: bool = Depends(verify_pin)):
    # Get the most recent undo-able sale
    five_minutes_ago = datetime.now() - timedelta(minutes=5)
    recent_undo = await undo_log_collection.find_one(
        {"timestamp": {"$gte": five_minutes_ago}},
        sort=[("timestamp", -1)]
    )
    
    if not recent_undo:
        raise HTTPException(status_code=400, detail="No recent sale to undo (within 5 minutes)")
    
    # Get the sale details
    try:
        sale_id = ObjectId(recent_undo["sale_id"])
    except:
        raise HTTPException(status_code=400, detail="Invalid sale ID in undo log")
    
    sale = await sales_collection.find_one({"_id": sale_id})
    if not sale:
        raise HTTPException(status_code=404, detail="Sale not found")
    
    # Revert product quantity
    try:
        product_id = ObjectId(sale["product_id"])
    except:
        raise HTTPException(status_code=400, detail="Invalid product ID in sale")
    
    await products_collection.update_one(
        {"_id": product_id},
        {"$inc": {"quantity": sale["quantity"]}}
    )
    
    # Revert bottle count if applicable
    if sale["bottle_taken"]:
        await bottles_collection.update_one(
            {"product_id": sale["product_id"]},
            {"$inc": {"bottles_taken": -sale["quantity"]}}
        )
    
    # Remove from sales and undo log
    await sales_collection.delete_one({"_id": sale_id})
    await undo_log_collection.delete_one({"_id": recent_undo["_id"]})
    
    return {"message": "Last sale undone successfully"}

# Bottle tracking endpoints
@app.post("/bottles/return")
async def return_bottles(bottle_return: BottleReturnWithCustomer, _: bool = Depends(verify_pin)):
    product = await products_collection.find_one({"name": bottle_return.product_name})
    if not product:
        raise HTTPException(status_code=404, detail="Product not found")
    if not product["is_bottled"]:
        raise HTTPException(status_code=400, detail="Product is not bottled")
    await bottles_collection.update_one(
        {"product_id": str(product["_id"])},
        {"$inc": {"bottles_returned": bottle_return.bottles_returned}},
        upsert=True
    )
    await db.bottle_returns.insert_one({
        "product_id": str(product["_id"]),
        "product_name": bottle_return.product_name,
        "bottles_returned": bottle_return.bottles_returned,
        "customer_name": bottle_return.customer_name,
        "timestamp": datetime.now()
    })
    return {"message": f"Returned {bottle_return.bottles_returned} bottles for {bottle_return.product_name} by {bottle_return.customer_name}"}

@app.get("/bottles/status")
async def get_bottle_status(_: bool = Depends(verify_pin)):
    bottle_status = []
    
    # Get all bottled products
    async for product in products_collection.find({"is_bottled": True}):
        bottle_data = await bottles_collection.find_one({"product_id": str(product["_id"])})
        
        taken = bottle_data["bottles_taken"] if bottle_data else 0
        returned = bottle_data["bottles_returned"] if bottle_data else 0
        outstanding = taken - returned
        
        bottle_status.append({
            "product_name": product["name"],
            "bottles_taken": taken,
            "bottles_returned": returned,
            "outstanding_bottles": outstanding
        })
    
    return bottle_status

# Daily summary endpoint
@app.post("/summary/daily", response_model=DailySummary)
async def get_daily_summary(date: Optional[str] = None, _: bool = Depends(verify_pin)):
    if date:
        try:
            target_date = datetime.fromisoformat(date).date()
        except:
            raise HTTPException(status_code=400, detail="Invalid date format")
    else:
        target_date = datetime.now().date()
    
    # Date range for the day
    start_of_day = datetime.combine(target_date, datetime.min.time())
    end_of_day = datetime.combine(target_date, datetime.max.time())
    
    # Get daily sales
    daily_sales = []
    total_sales_amount = 0
    sales_by_seller = {}
    
    async for sale in sales_collection.find({
        "timestamp": {"$gte": start_of_day, "$lte": end_of_day}
    }):
        daily_sales.append(sale)
        total_sales_amount += sale["total_amount"]
        
        seller = sale["sold_by"]
        if seller not in sales_by_seller:
            sales_by_seller[seller] = 0
        sales_by_seller[seller] += sale["total_amount"]
    
    # Get bottle statistics
    total_bottles_taken = 0
    total_bottles_returned = 0
    
    async for bottle_data in bottles_collection.find():
        total_bottles_taken += bottle_data.get("bottles_taken", 0)
        total_bottles_returned += bottle_data.get("bottles_returned", 0)
    
    # Get low stock products
    low_stock_products = []
    settings = await settings_collection.find_one({})
    threshold = settings.get("low_stock_threshold", 5) if settings else 5
    
    async for product in products_collection.find({"quantity": {"$lt": threshold}}):
        low_stock_products.append(product_dict_to_response(product))
    
    return DailySummary(
        date=target_date.isoformat(),
        total_sales_amount=total_sales_amount,
        sales_by_seller=sales_by_seller,
        bottles_taken=total_bottles_taken,
        bottles_returned=total_bottles_returned,
        outstanding_bottles=total_bottles_taken - total_bottles_returned,
        low_stock_products=low_stock_products
    )

# Export endpoint (stub)
@app.get("/export/daily-summary")
async def export_daily_summary(date: Optional[str] = None, _: bool = Depends(verify_pin)):
    # This is a stub for PDF export functionality
    return {
        "message": "PDF export feature coming soon",
        "data": "This would contain PDF data or download link",
        "note": "Implement with reportlab or similar PDF library"
    }

# Receipt preview endpoint
@app.get("/receipt/preview/{sale_id}")
async def get_receipt_preview(sale_id: str, _: bool = Depends(verify_pin)):
    try:
        sale = await sales_collection.find_one({"_id": ObjectId(sale_id)})
    except:
        raise HTTPException(status_code=400, detail="Invalid sale ID")
    
    if not sale:
        raise HTTPException(status_code=404, detail="Sale not found")
    
    receipt = {
        "receipt_id": sale_id,
        "product": sale["product_name"],
        "quantity": sale["quantity"],
        "unit_price": sale["unit_price"],
        "total_amount": sale["total_amount"],
        "seller": sale["sold_by"],
        "payment_method": sale["payment_method"],
        "timestamp": sale["timestamp"].isoformat(),
        "bottle_taken": sale.get("bottle_taken", False)
    }
    
    return {"receipt": receipt}

# PDF receipt endpoint
@app.get("/receipt/pdf/{sale_id}")
async def get_receipt_pdf(sale_id: str, _: bool = Depends(verify_pin)):
    sale = await sales_collection.find_one({"_id": ObjectId(sale_id)})
    if not sale:
        raise HTTPException(status_code=404, detail="Sale not found")
    buffer = BytesIO()
    p = canvas.Canvas(buffer)
    p.drawString(100, 800, "Sales Receipt")
    p.drawString(100, 780, f"Sale ID: {sale_id}")
    p.drawString(100, 760, f"Date: {sale['timestamp']}")
    y = 740
    if "items" in sale:
        for item in sale["items"]:
            p.drawString(100, y, f"{item['product_name']} x{item['quantity']} - ₦{item['total_amount']}")
            y -= 20
    else:
        p.drawString(100, y, f"{sale['product_name']} x{sale['quantity']} - ₦{sale['total_amount']}")
        y -= 20
    p.drawString(100, y-20, f"Total: ₦{sale.get('total_amount', 0)}")
    p.save()
    buffer.seek(0)
    return StreamingResponse(buffer, media_type="application/pdf", headers={"Content-Disposition": f"attachment; filename=receipt_{sale_id}.pdf"})

# Health check
@app.get("/health")
async def health_check():
    return {"status": "healthy", "timestamp": datetime.now()}

# Startup event
@app.on_event("startup")
async def startup_event():
    # Initialize default settings if not exists
    settings = await settings_collection.find_one({})
    if not settings:
        default_pin_hash = hash_pin("1234")
        await settings_collection.insert_one({
            "pin": default_pin_hash,
            "low_stock_threshold": 5,
            "receipt_preview": True
        })
        print("Initialized default settings with PIN: 1234")

# Add to your summary or a new endpoint
@app.get("/customers/{customer_id}/balance")
async def get_customer_balance(customer_id: str, _: bool = Depends(verify_pin)):
    # Sum all sales for this customer where payment_method == "Credit"
    pipeline = [
        {"$match": {"customer_id": customer_id, "payment_method": "Credit"}},
        {"$group": {"_id": None, "total_credit": {"$sum": "$total_amount"}}}
    ]
    result = await sales_collection.aggregate(pipeline).to_list(1)
    total_credit = result[0]["total_credit"] if result else 0
    return {"customer_id": customer_id, "outstanding_balance": total_credit}

@app.post("/customers", response_model=CustomerResponse)
async def add_customer(customer: Customer, _: bool = Depends(verify_pin)):
    result = await db.customers.insert_one(customer.dict())
    customer_dict = customer.dict()
    customer_dict["id"] = str(result.inserted_id)
    return CustomerResponse(**customer_dict)

@app.get("/customers", response_model=List[CustomerResponse])
async def list_customers(_: bool = Depends(verify_pin)):
    customers = []
    async for cust in db.customers.find():
        cust["id"] = str(cust["_id"])
        customers.append(CustomerResponse(**cust))
    return customers

if __name__ == "__main__":
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=8000,
        reload=True,
        log_level="info"
    )