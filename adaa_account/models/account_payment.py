# -*- coding: utf-8 -*-

from openerp import models, fields, api, _
from openerp.exceptions import UserError, ValidationError
import openerp.addons.decimal_precision as dp

MAP_INVOICE_TYPE_PARTNER_TYPE = {
    'out_invoice': 'customer',
    'out_refund': 'customer',
    'in_invoice': 'supplier',
    'in_refund': 'supplier',
}
# Since invoice amounts are unsigned, this is how we know if money comes in or goes out
MAP_INVOICE_TYPE_PAYMENT_SIGN = {
    'out_invoice': 1,
    'in_refund': 1,
    'in_invoice': -1,
    'out_refund': -1,
}

class account_register_payments(models.TransientModel):
    _inherit = "account.register.payments"
    
    def _get_register_invoices(self):
        return self.env['account.invoice'].browse(self._context.get('active_ids'))
    
    def _get_register_lines(self, register_ids):
        registers = []
        if register_ids:
            for register in register_ids:
                registers.append(register.id)
        return self.env['account.register.line'].browse(registers)

    register_ids = fields.One2many('account.register.line', 'register_id', copy=False, string='Register Invoice')
    
    @api.model
    def default_get(self, fields):
        rec = super(account_register_payments, self).default_get(fields)
        context = dict(self._context or {})
        active_model = context.get('active_model')
        active_ids = context.get('active_ids')
#         
        reg_lines = []
        for invoice in self.env[active_model].browse(active_ids):
            if invoice.origin:
                name = invoice.number +':'+ invoice.origin
            else:
                name = invoice.number
            reg_lines.append([0, 0, {
               'invoice_id': invoice.id,
               'name':  name,
               'amount_total': invoice.amount_total,
               'residual': invoice.residual,
               'amount_to_pay': invoice.residual,
               }])
        rec.update({
            'register_ids': reg_lines,
        })
        return rec
    
    @api.onchange('register_ids')
    def _onchange_register_ids(self):
        amount = 0.0
        for line in self.register_ids:
            amount += line.amount_to_pay
        self.amount = amount
        return
    
    def get_payment_line_vals(self, payment, line):
        """ Hook for extension """
        return {
            'payment_id': payment.id,
            'name': line.name,
            'invoice_id': line.invoice_id.id,
            'amount_total': line.amount_total,
            'residual': line.residual,
            'amount_to_pay': line.amount_to_pay,
        }
    
    @api.multi
    def create_payment(self):
        payment = self.env['account.payment'].create(self.get_payment_vals())
        if payment:
            for line in self._get_register_lines(self.register_ids):
                self.env['account.payment.line'].create(self.get_payment_line_vals(payment, line))
        payment.post_aos()
        return {'type': 'ir.actions.act_window_close'}

class account_register_line(models.TransientModel):
    _name = 'account.register.line'
    _description = 'Account Line Register'
    
    register_id = fields.Many2one('account.register.payments', string='Register Payment')
    name = fields.Char(string='Description', required=True)
    invoice_id = fields.Many2one('account.invoice', string='Invoice')
    amount_total = fields.Float('Amount Invoice', required=True, digits=dp.get_precision('Account'))
    residual = fields.Float('Residual', required=True, digits=dp.get_precision('Account'))
    amount_to_pay = fields.Float('Amount to Pay', required=True, digits=dp.get_precision('Account'))

class account_payment(models.Model):
    _inherit = "account.payment"
    register_ids = fields.One2many('account.payment.line', 'payment_id', copy=False, string='Register Invoice')
    
    @api.multi
    def post_aos(self):
        """ create post_aos without changing def post"""
        for rec in self:

            if rec.state != 'draft':
                raise UserError(_("Only a draft payment can be posted. Trying to post a payment in state %s.") % rec.state)

            if any(inv.state != 'open' for inv in rec.invoice_ids):
                raise ValidationError(_("The payment cannot be processed because the invoice is not open!"))

            # Use the right sequence to set the name
            if rec.payment_type == 'transfer':
                sequence = rec.env.ref('account.sequence_payment_transfer')
            else:
                if rec.partner_type == 'customer':
                    if rec.payment_type == 'inbound':
                        sequence = rec.env.ref('account.sequence_payment_customer_invoice')
                    if rec.payment_type == 'outbound':
                        sequence = rec.env.ref('account.sequence_payment_customer_refund')
                if rec.partner_type == 'supplier':
                    if rec.payment_type == 'inbound':
                        sequence = rec.env.ref('account.sequence_payment_supplier_refund')
                    if rec.payment_type == 'outbound':
                        sequence = rec.env.ref('account.sequence_payment_supplier_invoice')
            rec.name = sequence.with_context(ir_sequence_date=rec.payment_date).next_by_id()

            # Create the journal entry
            amount = rec.amount * (rec.payment_type in ('outbound', 'transfer') and 1 or -1)
            move = self.env['account.move'].create(self._get_move_vals())

            total_amount = 0.0
            for line in rec.register_ids:
                #create receivable or payable each invoice
                move = rec._create_payment_entry_aos(line.amount_to_pay * (rec.payment_type in ('outbound', 'transfer') and 1 or -1), line.invoice_id, move)
                total_amount += (line.amount_to_pay * (rec.payment_type in ('outbound', 'transfer') and -1 or 1))
            if move:
                #accumulate all amount
                move = rec._create_liquidity_entry_aos(total_amount, move)

            # In case of a transfer, the first journal entry created debited the source liquidity account and credited
            # the transfer account. Now we debit the transfer account and credit the destination liquidity account.
            if rec.payment_type == 'transfer':
                transfer_credit_aml = move.line_ids.filtered(lambda r: r.account_id == rec.company_id.transfer_account_id)
                transfer_debit_aml = rec._create_transfer_entry(amount)
                (transfer_credit_aml + transfer_debit_aml).reconcile()
           
            rec.state = 'posted'
    
    def _create_liquidity_entry_aos(self, total_amount, move):
        """ def _create_liquidity_entry_aos for total liquidity received or paid"""
        aml_obj = self.env['account.move.line'].with_context(check_move_validity=False)
        debit, credit, amount_currency = aml_obj.with_context(date=self.payment_date).compute_amount_fields(total_amount, self.currency_id, self.company_id.currency_id)
        #print "----_create_liquidity_entry_aos----",debit, credit, amount_currency
        liquidity_aml_dict = self._get_shared_move_line_vals(debit, credit, amount_currency, move.id, False)
        liquidity_aml_dict.update(self._get_counterpart_move_line_vals(self.invoice_ids))
        liquidity_aml_dict.update(self._get_liquidity_move_line_vals(total_amount))
        aml_obj.create(liquidity_aml_dict)
        move.post()
        return move
    
    def _create_payment_entry_aos(self, amount, invoice, move):
        """ def _create_payment_entry_aos without changing base function def _create_payment_entry"""
        aml_obj = self.env['account.move.line'].with_context(check_move_validity=False)
        debit, credit, amount_currency = aml_obj.with_context(date=self.payment_date).compute_amount_fields(amount, self.currency_id, self.company_id.currency_id)
        #print "====_create_payment_entry_aos===",debit, credit, amount_currency, move
        #Write line corresponding to invoice payment
        counterpart_aml_dict = self._get_shared_move_line_vals(debit, credit, amount_currency, move.id, invoice)
        counterpart_aml_dict.update(self._get_counterpart_move_line_vals())
        counterpart_aml_dict.update({'currency_id': self.currency_id != self.company_id.currency_id and self.currency_id.id or False})
        counterpart_aml = aml_obj.create(counterpart_aml_dict)
        #Reconcile with the invoices each
        if self.payment_difference_handling == 'reconcile':
            invoice.register_payment(counterpart_aml, self.writeoff_account_id, self.journal_id)
        else:
            invoice.register_payment(counterpart_aml)
        return move
    
class account_payment_line(models.Model):
    _name = 'account.payment.line'
    _description = 'Account Line Register'
    
    payment_id = fields.Many2one('account.payments', string='Payment')
    name = fields.Char(string='Description', required=True)
    invoice_id = fields.Many2one('account.invoice', string='Invoice')
    amount_total = fields.Float('Amount Invoice', required=True, digits=dp.get_precision('Account'))
    residual = fields.Float('Residual', required=True, digits=dp.get_precision('Account'))
    amount_to_pay = fields.Float('Amount to Pay', required=True, digits=dp.get_precision('Account'))
    
    