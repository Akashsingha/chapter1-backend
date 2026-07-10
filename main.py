from fastapi import FastAPI
from pydantic import BaseModel
from database import supabase #importing supabse variable which is the return of client id (url,api)
from fastapi.middleware.cors import CORSMiddleware # it allows communication between teo different port 3000(frontend), and 8000(backend)
import os


app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins = ["http://localhost:3000","https://chapter1-menu-lppv.vercel.app"],             
    allow_methods=["*"],
    allow_headers=["*"])

DASHBOARD_PASSWORD = os.getenv("DASHBOARD_PASSWORD")

@app.get("/menu")  #"Go to menu_items table, get everything, give it back to me."

def get_menu():
    response = supabase.table("menu_items").select("*").execute() #giving an sql comment to supabse to display all the items in menu_items table
    return response.data  # returning the response to the frontend

class OrderItem(BaseModel):
    name: str
    price: int
    quantity: int

class Order(BaseModel):
    customer_name : str
    customer_phone: str
    items : list[OrderItem]
    payment_method: str = "cash"


#when goes to orders tab 
@app.post("/orders")
def create_order(order: Order):
    total = 0   # doing the total calculation by multiplying item price with no of quantity
    for item in order.items:
        total = total + (item.price * item.quantity) 


    # Step 1 - save order header
    order_response = supabase.table("orders").insert({
        "customer_name": order.customer_name,
        "customer_phone": order.customer_phone,
        "total_amount": total,
        "status": "received",
        "payment_method": order.payment_method,
        "payment_status": "pending"
    }).execute()

    order_id = order_response.data[0]["id"]
    
    for item in order.items:
        supabase.table("order_items").insert({
        "order_id": order_id,
        "item_name": item.name,
        "quantity": item.quantity,
        "price": item.price,
    }).execute()

    return {
        "id": order_id,
        "customer_name": order.customer_name,
        "total": total,
        "total_amount": total,
        "status": "received",
        "payment_method": order.payment_method,
        "payment_status": "pending"
    }

class StatusUpdate(BaseModel):
    status: str

@app.patch("/orders/{order_id}/status")
def update_order_status(order_id: str, body: StatusUpdate):
    supabase.table('orders').update({
        "status": body.status
    }).eq("id", order_id).execute()
    # update orders table
    # where id = order_id
    # set status = new status
    return {"message": "order updated"}

@app.get("/orders/{order_id}")
def get_order(order_id: str):
    response = supabase.table("orders").select("*").eq("id", order_id).execute()
    if not response.data:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="Order not found")
    order = response.data[0]
    # Fetch order items
    items_response = supabase.table("order_items").select("*").eq("order_id", order_id).execute()
    order["items"] = items_response.data
    return order

@app.get("/orders")
def get_orders():
    response = supabase.table("orders").select("*").execute()
    return response.data


class PasswordCheck(BaseModel):
    password: str

@app.post("/verify-dashboard-password")
def verify_dashboard_password(body: PasswordCheck):
    if body.password == DASHBOARD_PASSWORD:
        return {"valid": True}
    return {"valid": False}

class PaymentConfirm(BaseModel):
    payment_status: str

@app.patch("/orders/{order_id}/payment")
def confirm_payment(order_id: str, body: PaymentConfirm):
    supabase.table('orders').update({
        "payment_status": body.payment_status
    }).eq("id", order_id).execute()
    return {"message": "payment status updated"}