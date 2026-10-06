-- ZM Mega Mall department/category seed.
-- Safe to run repeatedly: existing product quantities and existing rows are preserved.

insert into public.categories (name, description)
values
    ('Grocery', 'Atta, rice, cooking oil, sugar, drinks, biscuits, and grocery items'),
    ('Clothing', 'Clothes, shirts, pants, shoes, and kids wear'),
    ('Electronics', 'Mobile phones, chargers, headphones, and small appliances'),
    ('Household', 'Household Items: soap, shampoo, detergent, and kitchen items'),
    ('Cosmetics', 'Perfume, creams, makeup, and personal-care products'),
    ('Toys & Stationery', 'Toys, notebooks, pens, school stationery, and office stationery'),
    ('Food Court / Restaurants', 'Food court and restaurant consumables'),
    ('Pharmacy', 'Non-prescription pharmacy and personal-care inventory only')
on conflict (name) do update set description = excluded.description;

-- Do not update quantity on conflict: inventory balances are operational data.
insert into public.products (
    name, sku, category_id, supplier_id, quantity, minimum_stock,
    selling_price, cost_price, location
)
select
    seed.name,
    seed.sku,
    (select id from public.categories where name = seed.category_name),
    (select id from public.suppliers where name = seed.supplier_name),
    seed.quantity,
    seed.minimum_stock,
    seed.selling_price,
    seed.cost_price,
    seed.location
from (
    values
        ('Atta 20kg', 'GROC-ATTA-020', 'Grocery', 'Nowshera Wholesale', 24, 6, 4200.00, 3600.00, 'Main Store'),
        ('Basmati Rice', 'GROC-RICE-BAS', 'Grocery', 'Nowshera Wholesale', 30, 8, 5200.00, 4450.00, 'Warehouse'),
        ('Cooking Oil', 'GROC-OIL-005', 'Grocery', 'Nowshera Wholesale', 36, 10, 1450.00, 1180.00, 'Aisle G2'),
        ('Sugar', 'GROC-SUG-001', 'Grocery', 'Nowshera Wholesale', 45, 10, 190.00, 155.00, 'Aisle G3'),
        ('Soft Drink', 'GROC-DRK-001', 'Grocery', 'Nowshera Wholesale', 60, 12, 180.00, 125.00, 'Billing Floor'),
        ('Biscuits', 'GROC-BIS-001', 'Grocery', 'Nowshera Wholesale', 50, 12, 120.00, 82.00, 'Aisle G4'),
        ('Men Shirt', 'CLO-SHT-MEN', 'Clothing', 'Ali Traders', 20, 5, 2400.00, 1500.00, 'Aisle C2'),
        ('Pants', 'CLO-PNT-001', 'Clothing', 'Ali Traders', 18, 5, 2800.00, 1750.00, 'Aisle C3'),
        ('Shoes', 'CLO-SHO-001', 'Clothing', 'Ali Traders', 16, 4, 3800.00, 2500.00, 'Aisle C4'),
        ('Kids Wear', 'CLO-KID-001', 'Clothing', 'Ali Traders', 22, 6, 2100.00, 1350.00, 'Aisle C5'),
        ('Mobile Phone', 'ELEC-MOB-001', 'Electronics', 'Pak Electronics', 12, 3, 32000.00, 28500.00, 'Aisle E3'),
        ('Charger', 'ELEC-CHG-001', 'Electronics', 'Pak Electronics', 28, 6, 1200.00, 750.00, 'Warehouse'),
        ('Headphones', 'ELEC-HDP-001', 'Electronics', 'Pak Electronics', 15, 4, 3500.00, 2200.00, 'Aisle E4'),
        ('Electric Kettle', 'ELEC-KTL-001', 'Electronics', 'Pak Electronics', 10, 3, 6500.00, 4800.00, 'Store Room'),
        ('Soap', 'HOME-SOP-001', 'Household', 'Ali Traders', 40, 10, 180.00, 120.00, 'Aisle H1'),
        ('Shampoo', 'HOME-SHM-001', 'Household', 'Ali Traders', 26, 6, 650.00, 440.00, 'Aisle H2'),
        ('Detergent', 'HOME-DTG-001', 'Household', 'Ali Traders', 32, 8, 850.00, 610.00, 'Aisle H3'),
        ('Kitchen Set', 'HOME-KIT-001', 'Household', 'Ali Traders', 14, 4, 4200.00, 2900.00, 'Store Room'),
        ('Perfume', 'COS-PER-001', 'Cosmetics', 'Ali Traders', 18, 5, 2800.00, 1800.00, 'Aisle K1'),
        ('Face Cream', 'COS-FCR-001', 'Cosmetics', 'Ali Traders', 25, 6, 950.00, 620.00, 'Aisle K2'),
        ('Makeup Item', 'COS-MUP-001', 'Cosmetics', 'Ali Traders', 20, 5, 1500.00, 980.00, 'Aisle K3'),
        ('Toy Car', 'TOY-CAR-001', 'Toys & Stationery', 'Ali Traders', 24, 6, 900.00, 560.00, 'Aisle T1'),
        ('Notebook', 'STA-NBK-001', 'Toys & Stationery', 'Ali Traders', 80, 20, 180.00, 110.00, 'Aisle S1'),
        ('Pen', 'STA-PEN-001', 'Toys & Stationery', 'Ali Traders', 120, 30, 60.00, 35.00, 'Billing Floor'),
        ('Food Court Water', 'FOOD-WTR-001', 'Food Court / Restaurants', 'Nowshera Wholesale', 100, 20, 80.00, 45.00, 'Food Court'),
        ('First Aid Kit', 'PHAR-FAK-001', 'Pharmacy', 'Ali Traders', 12, 3, 1800.00, 1200.00, 'Pharmacy Counter')
) as seed(name, sku, category_name, supplier_name, quantity, minimum_stock, selling_price, cost_price, location)
on conflict (sku) do update set
    name = excluded.name,
    category_id = excluded.category_id,
    supplier_id = excluded.supplier_id,
    minimum_stock = excluded.minimum_stock,
    selling_price = excluded.selling_price,
    cost_price = excluded.cost_price,
    location = excluded.location,
    is_active = true;
