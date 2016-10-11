# -*- coding: utf-8 -*-
# Part of Odoo. See LICENSE file for full copyright and licensing details.
{
    'name' : 'Payment multiple Invoice',
    'version' : '1.1',
    'summary': 'Payment multiple Invoice for Selected Invoice and Add amount',
    'sequence': 1,
    "author": "Adaa Saas Solution",
    'description': """
Invoicing & Payments
====================
    """,
    'category' : 'Adaa Accounting & Finance',
    'website': 'https://www.adaa.com/',
    'images' : [],
    'depends' : ['account'],
    'data': [
        "security/ir.model.access.csv",
        "wizards/account_payment_view.xml",
    ],
    'demo': [
        
    ],
    'qweb': [
        
    ],
    'price': 25.00,
    'currency': 'EUR',
    'installable': True,
    'auto_install': False,
    #'post_init_hook': '_auto_install_l10n',
}
