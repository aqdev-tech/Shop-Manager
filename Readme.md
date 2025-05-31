# Smart Inventory & Sales Tracking

A modern inventory and sales management system for small shops, bars, and provision stores. Built with **FastAPI**, **MongoDB**, and a responsive **Tailwind CSS** frontend.

---

## Features

- **Product Management:** Add, edit, delete, and track products (including bottled products).
- **Sales Tracking:** Record single or multiple product sales, with support for various payment methods.
- **Bottles Management:** Track bottles taken and returned for bottled products.
- **Daily Summary:** View daily sales, outstanding bottles, and low stock alerts.
- **Customer Management:** Add customers and track outstanding credit balances.
- **Authentication:** Simple PIN-based login and PIN change.
- **PDF Receipts:** Generate and download PDF receipts for sales.
- **Undo Sales:** Undo the last sale within 5 minutes.
- **Barcode Scanning:** Scan product barcodes for quick sales entry (via QuaggaJS).
- **Responsive UI:** Clean, mobile-friendly interface using Tailwind CSS.

---

## Tech Stack

- **Backend:** FastAPI, Motor (async MongoDB), Pydantic, ReportLab, python-dotenv
- **Frontend:** HTML, Tailwind CSS, Vanilla JS, QuaggaJS (barcode scanning)
- **Database:** MongoDB

---

## Getting Started

### Prerequisites

- Python 3.8+
- MongoDB (local or remote)

### Installation

1. **Clone the repository:**
    ```sh
    git clone https://github.com/aqdev-tech/shop-manager.git
    cd shop-manager
    ```

2. **Install dependencies:**
    ```sh
    pip install -r requirements.txt
    ```

3. **Configure environment variables:**

    Create a `.env` file:

    ```
    MONGODB_URL=mongodb://localhost:27017
    ```

4. **Run the backend server:**
    ```sh
    uvicorn main:app --reload
    ```

5. **Open the frontend:**

    Open `index.html` in your browser (or serve it with a simple HTTP server).

---

## Usage

- **Login:** Use the default PIN `1234` (change it in the Settings page).
- **Add Products:** Go to Products, add your inventory.
- **Record Sales:** Go to Sales, select products, and record sales.
- **View Dashboard & Summary:** See stats and low stock alerts.
- **Manage Customers:** Add customers and track credit sales.
- **Undo Sale:** Undo the last sale within 5 minutes.
- **Barcode Scanning:** Use the "Scan Barcode" button in the sales form.

---

## API Endpoints

- `/auth/login` - Login with PIN
- `/auth/change-pin` - Change PIN
- `/products` - CRUD for products
- `/sales` - Record and list sales
- `/sales/multi` - Record multi-product sales
- `/sales/undo-last` - Undo last sale
- `/bottles/return` - Record bottle returns
- `/summary/daily` - Get daily summary
- `/customers` - Add/list customers
- `/customers/{customer_id}/balance` - Get customer outstanding balance
- `/receipt/pdf/{sale_id}` - Download PDF receipt

See `main.py` for full API details.

---

## Customization

- **Low Stock Threshold:** Change in MongoDB `settings` collection (`low_stock_threshold`).
- **Default PIN:** First run initializes with `1234`.

---

## License

MIT License

---

## Acknowledgements

- [FastAPI](https://fastapi.tiangolo.com/)
- [Tailwind CSS](https://tailwindcss.com/)
- [QuaggaJS](https://github.com/ericblade/quagga2)
- [ReportLab](https://www.reportlab.com/)

---

## Screenshots

> Add screenshots of the dashboard, products, sales, and summary pages here.

---

## Contributing

Pull requests are welcome! For major changes, please open an issue first to discuss what you would like to change.

---

## Author

- [aqdev-tech](https://github.com/aqdev-tech)