from fastapi import FastAPI
from pydantic import BaseModel
from database import supabase #importing supabse variable which is the return of client id (url,api)
from fastapi.middleware.cors import CORSMiddleware # it allows communication between teo different port 3000(frontend), and 8000(backend)

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins = ["http://localhost:3000","https://chapter1-menu-lppv.vercel.app","https://chapter1-frontend-lppv.vercel.app", ],             
    allow_methods=["*"],
    allow_headers=["*"])

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
        "status": "received"
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
        "customer_name": order.customer_name,
        "total": total,
        "status": "received"
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

@app.get("/orders")
def get_orders():
    response = supabase.table("orders").select("*").execute()
    return response.data
