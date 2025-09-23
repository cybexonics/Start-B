from datetime import datetime
from bson import ObjectId
from typing import List, Optional

class User:
    def __init__(self, username: str, password: str, role: str = 'user'):
        self.username = username
        self.password = password
        self.role = role
        self.created_at = datetime.now()
        self.updated_at = datetime.now()
    
    def to_dict(self):
        return {
            'username': self.username,
            'password': self.password,
            'role': self.role,
            'created_at': self.created_at,
            'updated_at': self.updated_at
        }

class Customer:
    def __init__(self, name: str, phone: str, 
                 email: Optional[str] = None, 
                 address: Optional[str] = None, 
                 notes: Optional[str] = None):
        self.name = name
        self.phone = phone
        self.email = email
        self.address = address
        self.notes = notes
        self.created_at = datetime.now()
        self.updated_at = datetime.now()
    
    def to_dict(self):
        return {
            'name': self.name,
            'phone': self.phone,
            'email': self.email,
            'address': self.address,
            'notes': self.notes,
            'created_at': self.created_at,
            'updated_at': self.updated_at
        }

class BillItem:
    def __init__(self, name: str, price: float, quantity: int = 1, description: Optional[str] = None):
        self.name = name
        self.description = description
        self.quantity = quantity
        self.price = price
    
    def to_dict(self):
        return {
            'name': self.name,
            'description': self.description,
            'quantity': self.quantity,
            'price': self.price
        }

class Bill:
    def __init__(self, customer_id: str, items: List[BillItem], 
                 discount: float = 0, advance: float = 0, 
                 status: str = 'pending', notes: Optional[str] = None):
        self.customer_id = ObjectId(customer_id)
        self.items = [item.to_dict() for item in items]
        self.subtotal = sum(item.price * item.quantity for item in items)
        self.discount = discount
        self.advance = advance
        self.balance = self.subtotal - discount - advance
        self.status = status
        self.notes = notes
        self.created_at = datetime.now()
        self.updated_at = datetime.now()
    
    def to_dict(self):
        return {
            'customer_id': self.customer_id,
            'items': self.items,
            'subtotal': self.subtotal,
            'discount': self.discount,
            'advance': self.advance,
            'balance': self.balance,
            'status': self.status,
            'notes': self.notes,
            'created_at': self.created_at,
            'updated_at': self.updated_at
        }

class Tailor:
    def __init__(self, name: str, phone: str, 
                 email: Optional[str] = None, 
                 specialization: Optional[str] = None, 
                 status: str = 'active'):
        self.name = name
        self.phone = phone
        self.email = email
        self.specialization = specialization
        self.status = status
        self.created_at = datetime.now()
        self.updated_at = datetime.now()
    
    def to_dict(self):
        return {
            'name': self.name,
            'phone': self.phone,
            'email': self.email,
            'specialization': self.specialization,
            'status': self.status,
            'created_at': self.created_at,
            'updated_at': self.updated_at
        }

class SystemSettings:
    def __init__(self, upi_id: str, business_name: str, 
                 business_address: Optional[str] = None, 
                 business_phone: Optional[str] = None):
        self.upi_id = upi_id
        self.business_name = business_name
        self.business_address = business_address
        self.business_phone = business_phone
        self.created_at = datetime.now()
        self.updated_at = datetime.now()
    
    def to_dict(self):
        return {
            'type': 'system_settings',
            'upi_id': self.upi_id,
            'business_name': self.business_name,
            'business_address': self.business_address,
            'business_phone': self.business_phone,
            'created_at': self.created_at,
            'updated_at': self.updated_at
        }

class WorkflowStage:
    """Represents a single stage in the tailoring workflow"""
    def __init__(self, name: str, status: str = 'pending', started_at: Optional[datetime] = None, 
                 completed_at: Optional[datetime] = None, assigned_tailor: Optional[str] = None, 
                 notes: Optional[str] = None):
        self.name = name  # 'cutting', 'stitching', 'finishing', 'packaging'
        self.status = status  # 'pending', 'in_progress', 'completed', 'on_hold'
        self.started_at = started_at
        self.completed_at = completed_at
        self.assigned_tailor = assigned_tailor  # Tailor ID responsible for this stage
        self.notes = notes
        self.updated_at = datetime.now()
    
    def to_dict(self):
        return {
            'name': self.name,
            'status': self.status,
            'started_at': self.started_at.isoformat() if self.started_at else None,
            'completed_at': self.completed_at.isoformat() if self.completed_at else None,
            'assigned_tailor': self.assigned_tailor,
            'notes': self.notes,
            'updated_at': self.updated_at.isoformat()
        }

class Job:
    """Enhanced Job model with workflow stages"""
    def __init__(self, title: str, description: str, bill_id: str, customer_id: str,
                 priority: str = 'medium', due_date: Optional[datetime] = None, 
                 created_by: Optional[str] = None):
        self.title = title
        self.description = description
        self.bill_id = ObjectId(bill_id) if bill_id else None
        self.customer_id = ObjectId(customer_id) if customer_id else None
        self.priority = priority  # 'low', 'medium', 'high', 'urgent'
        self.due_date = due_date
        self.created_by = created_by
        self.status = 'assigned'  # Overall job status
        
        # Initialize workflow stages
        self.workflow_stages = [
            WorkflowStage('cutting').to_dict(),
            WorkflowStage('stitching').to_dict(),
            WorkflowStage('finishing').to_dict(),
            WorkflowStage('packaging').to_dict()
        ]
        
        # Progress tracking
        self.current_stage = 'cutting'
        self.progress_percentage = 0
        
        # Timestamps
        self.created_at = datetime.now()
        self.updated_at = datetime.now()
    
    def to_dict(self):
        return {
            'title': self.title,
            'description': self.description,
            'bill_id': self.bill_id,
            'customer_id': self.customer_id,
            'priority': self.priority,
            'due_date': self.due_date.isoformat() if self.due_date else None,
            'created_by': self.created_by,
            'status': self.status,
            'workflow_stages': self.workflow_stages,
            'current_stage': self.current_stage,
            'progress_percentage': self.progress_percentage,
            'created_at': self.created_at,
            'updated_at': self.updated_at
        }
