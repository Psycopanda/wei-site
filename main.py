from fastapi import FastAPI, HTTPException, Query, status
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import List, Optional
from datetime import datetime
import os
from pymongo import MongoClient
from bson import ObjectId

app = FastAPI(title="WEI CentraleSupélec API", version="1.0.0")

# Configuration CORS pour autoriser le frontend
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Identifiants Admin (Modifiables via variables d'environnement)
ADMIN_EMAIL = os.getenv("ADMIN_EMAIL", "admin@wei.fr")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "WeiCentrale2026!")
MONGO_URI = os.getenv(
    "MONGO_URI", 
    "mongodb+srv://wei2027:weisecretkey@cluster0.owsmk8v.mongodb.net/?appName=Cluster0"
)

try:
    client = MongoClient(MONGO_URI)
    db = client.wei_db
    client.admin.command('ping')
    print("✅ Connexion réussie à MongoDB Atlas !")
    orders_collection = db["orders"]
    collocs_collection = db["collocs"]
except Exception as e:
    print("❌ Erreur de connexion MongoDB :", e)


# Modèles Pydantic
class OrderItem(BaseModel):
    id: Optional[str] = None
    name: str
    price: float
    quantity: int

class OrderCreate(BaseModel):
    client_name: str
    phone: str
    colloc_name: str
    delivery: bool = False
    delivery_address: Optional[str] = ""
    payment_method: str
    items: List[OrderItem]
    total_price: float
    is_paid: bool = False

class AdminLoginRequest(BaseModel):
    email: str
    password: str

# Utilitaire de sérialisation ObjectId
def serialize_doc(doc):
    if not doc:
        return None
    doc["_id"] = str(doc["_id"])
    return doc


# ============================================================================
# ENDPOINTS API
# ============================================================================

@app.post("/api/admin/login")
def admin_login(credentials: AdminLoginRequest):
    """Vérification sécurisée des identifiants admin."""
    if credentials.email.strip().lower() == ADMIN_EMAIL.lower() and credentials.password == ADMIN_PASSWORD:
        return {
            "success": True,
            "message": "Connexion admin réussie",
            "token": "wei-admin-authenticated-session"
        }
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Identifiants administration incorrects."
    )


@app.post("/api/orders", status_code=status.HTTP_201_CREATED)
def create_order(order: OrderCreate):
    """Création d'une commande et crédit de la coloc."""
    order_dict = order.dict()
    order_dict["colloc_name"] = order_dict["colloc_name"].strip().upper()
    order_dict["status"] = "pending"
    order_dict["timestamp"] = datetime.utcnow().isoformat()
    
    result = orders_collection.insert_one(order_dict)
    order_id = str(result.inserted_id)
    
    # Créditer la coloc dans le classement
    collocs_collection.update_one(
        {"name": order_dict["colloc_name"]},
        {
            "$inc": {
                "total_spent": order_dict["total_price"],
                "order_count": 1
            }
        },
        upsert=True
    )
    
    return {"order_id": order_id, "message": "Commande enregistrée avec succès"}


@app.get("/api/orders")
def get_orders():
    """Récupération de toutes les commandes."""
    orders = list(orders_collection.find().sort("timestamp", -1))
    return [serialize_doc(o) for o in orders]


@app.put("/api/orders/{order_id}/status")
def update_order_status(order_id: str, new_status: str = Query(...)):
    """Mise à jour du statut d'avancement de la commande."""
    try:
        obj_id = ObjectId(order_id)
    except Exception:
        raise HTTPException(status_code=400, detail="ID de commande invalide")
        
    result = orders_collection.update_one(
        {"_id": obj_id},
        {"$set": {"status": new_status}}
    )
    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail="Commande non trouvée")
    return {"message": "Statut mis à jour"}


@app.put("/api/orders/{order_id}/paid")
def update_order_paid(order_id: str, is_paid: bool = Query(...)):
    """Mise à jour du statut de paiement (Payé / Non Payé)."""
    try:
        obj_id = ObjectId(order_id)
    except Exception:
        raise HTTPException(status_code=400, detail="ID de commande invalide")
        
    result = orders_collection.update_one(
        {"_id": obj_id},
        {"$set": {"is_paid": is_paid}}
    )
    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail="Commande non trouvée")
    return {"message": "Statut de paiement mis à jour"}


@app.delete("/api/orders/{order_id}")
def delete_order(order_id: str):
    """Suppression d'une commande et débit du montant sur la coloc."""
    try:
        obj_id = ObjectId(order_id)
    except Exception:
        raise HTTPException(status_code=400, detail="ID de commande invalide")
    
    # 1. Récupérer la commande pour connaître la coloc et le montant
    order = orders_collection.find_one({"_id": obj_id})
    if not order:
        raise HTTPException(status_code=404, detail="Commande non trouvée")
        
    colloc_name = order.get("colloc_name", "").upper()
    total_price = float(order.get("total_price", 0.0))
    
    # 2. Supprimer la commande de la base
    orders_collection.delete_one({"_id": obj_id})
    
    # 3. Débiter le montant et décrémenter le nombre de commandes de la coloc
    if colloc_name:
        collocs_collection.update_one(
            {"name": colloc_name},
            {
                "$inc": {
                    "total_spent": -total_price,
                    "order_count": -1
                }
            }
        )
        # Sécurité : Éviter tout solde ou compteur négatif
        collocs_collection.update_one(
            {"name": colloc_name, "total_spent": {"$lt": 0}},
            {"$set": {"total_spent": 0.0}}
        )
        collocs_collection.update_one(
            {"name": colloc_name, "order_count": {"$lt": 0}},
            {"$set": {"order_count": 0}}
        )
        
    return {
        "message": f"Commande {order_id} supprimée. {total_price:.2f}€ débités de la coloc {colloc_name}.",
        "order_id": order_id
    }


@app.get("/api/ranking")
def get_ranking():
    """Récupération du classement des colocs."""
    rankings = list(collocs_collection.find().sort("total_spent", -1))
    return [serialize_doc(r) for r in rankings]
