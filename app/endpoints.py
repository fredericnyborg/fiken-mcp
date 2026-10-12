"""Register over Fiken API v2-endepunkter som eksponeres 1:1 som MCP-verktøy.

Hver rad er ett endepunkt: verktøynavn, HTTP-metode, sti (relativ til
/companies/{companySlug} når company=True), type (read/write/destructive) og
beskrivelse. Verktøyene er tynne: sti-parametre blir egne argumenter, query
sendes som `query`, og request-body sendes rått som `body` (Fikens eget format).

Endepunktlisten og beskrivelsene er avledet fra fjordbook-mcp (MIT, se NOTICE),
som igjen følger Fikens offisielle OpenAPI-spesifikasjon.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

Kind = Literal["read", "write", "destructive"]


@dataclass(frozen=True)
class Endpoint:
    name: str
    method: str
    path: str
    kind: Kind
    description: str
    company: bool = True
    paged: bool = False
    body: bool = False
    required_query: tuple[str, ...] = ()
    optional_query: tuple[str, ...] = ()
    filters: tuple[str, ...] = ()
    validate: str | None = None
    path_params: tuple[str, ...] = field(init=False)

    def __post_init__(self) -> None:
        import re

        object.__setattr__(self, "path_params", tuple(re.findall(r"\{(\w+)\}", self.path)))


E = Endpoint

# Sti-parametre som er tekst (resten er heltalls-ID-er).
STRING_PATH_PARAMS = {"accountCode", "attachmentUuid"}

JOURNAL_ENTRY_DESCRIPTION = (
    "Creates a general journal entry (fri postering / bilag). Body: "
    "{description?, open?, journalEntries: [{description, date: YYYY-MM-DD, lines: "
    "[{amount, debitAccount?, debitVatCode?, creditAccount?, creditVatCode?, account?, vatCode?, "
    "projectId?: [int]}]}]}. Amounts are integers in NOK øre (100 = 1 kr). A line with only "
    "debitAccount is debit, only creditAccount is credit, both = self-balancing line. Without VAT "
    "codes each journal entry must balance (checked before sending). With VAT codes the debit "
    "amount is net and credit is gross, and Fiken validates the balance."
)

ENDPOINTS: tuple[Endpoint, ...] = (
    E('list_accounts', 'GET', '/accounts', 'read', 'Retrieves bookkeeping accounts for the current year', paged=True, filters=('fromAccount', 'toAccount', 'range')),
    E('get_account', 'GET', '/accounts/{accountCode}', 'read', 'Retrieves a specific bookkeeping account by account code'),
    E('list_account_balances', 'GET', '/accountBalances', 'read', 'Retrieves accounts and closing balances for a given date', paged=True, filters=('date', 'fromAccount', 'toAccount')),
    E('get_account_balance', 'GET', '/accountBalances/{accountCode}', 'read', 'Retrieves a specific account and its balance for a given date', filters=('date',)),
    E('list_bank_accounts', 'GET', '/bankAccounts', 'read', 'Retrieves all bank accounts for the company', paged=True, filters=('inactive',)),
    E('create_bank_account', 'POST', '/bankAccounts', 'write', 'Creates a new bank account for the company', body=True),
    E('get_bank_account', 'GET', '/bankAccounts/{bankAccountId}', 'read', 'Retrieves a specific bank account by ID'),
    E('list_bank_balances', 'GET', '/bankBalances', 'read', 'Retrieves bank balances for the company at a given date', paged=True, filters=('date',)),
    E('list_contacts', 'GET', '/contacts', 'read', 'Retrieves all contacts for the company. Supports many filters.', paged=True, filters=('sortBy', 'supplierNumber', 'customerNumber', 'memberNumber', 'memberNumberString', 'name', 'organizationNumber', 'email', 'phoneNumber', 'customer', 'supplier', 'inactive', 'group', 'lastModified', 'lastModifiedLe', 'lastModifiedLt', 'lastModifiedGe', 'lastModifiedGt', 'createdDate', 'createdDateLe', 'createdDateLt', 'createdDateGe', 'createdDateGt')),
    E('create_contact', 'POST', '/contacts', 'write', 'Creates a new contact (customer/supplier)', body=True),
    E('get_contact', 'GET', '/contacts/{contactId}', 'read', 'Retrieves a specific contact by ID'),
    E('update_contact', 'PUT', '/contacts/{contactId}', 'destructive', 'Replaces an existing contact (PUT: all fields are overwritten, so name is required and omitted optional fields are cleared). Replaces the whole record: send every field you want to keep.', body=True),
    E('delete_contact', 'DELETE', '/contacts/{contactId}', 'destructive', 'Deletes a contact, or marks it inactive if it has associated records'),
    E('list_contact_persons', 'GET', '/contacts/{contactId}/contactPerson', 'read', 'Retrieves all contact persons for a contact'),
    E('create_contact_person', 'POST', '/contacts/{contactId}/contactPerson', 'write', 'Adds a contact person to a contact', body=True),
    E('get_contact_person', 'GET', '/contacts/{contactId}/contactPerson/{contactPersonId}', 'read', 'Retrieves a specific contact person'),
    E('update_contact_person', 'PUT', '/contacts/{contactId}/contactPerson/{contactPersonId}', 'destructive', 'Replaces a contact person (PUT: name and email are required). Replaces the whole record: send every field you want to keep.', body=True),
    E('delete_contact_person', 'DELETE', '/contacts/{contactId}/contactPerson/{contactPersonId}', 'destructive', 'Deletes a contact person'),
    E('list_groups', 'GET', '/groups', 'read', 'Returns all customer groups for the company', paged=True),
    E('list_credit_notes', 'GET', '/creditNotes', 'read', 'Returns all credit notes for the company', paged=True, filters=('issueDate', 'issueDateLe', 'issueDateLt', 'issueDateGe', 'issueDateGt', 'lastModified', 'lastModifiedLe', 'lastModifiedLt', 'lastModifiedGe', 'lastModifiedGt', 'customerId', 'settled', 'creditNoteDraftUuid')),
    E('get_credit_note', 'GET', '/creditNotes/{creditNoteId}', 'read', 'Returns a specific credit note by ID'),
    E('create_full_credit_note', 'POST', '/creditNotes/full', 'write', 'Creates a credit note covering the full amount of an invoice', body=True),
    E('create_partial_credit_note', 'POST', '/creditNotes/partial', 'write', 'Creates a credit note for a partial amount of an invoice. Lines must total less than the original invoice.', body=True),
    E('send_credit_note', 'POST', '/creditNotes/send', 'write', 'Sends a credit note via email and/or EHF', body=True),
    E('get_credit_note_counter', 'GET', '/creditNotes/counter', 'read', 'Retrieves the current credit note number counter'),
    E('create_credit_note_counter', 'POST', '/creditNotes/counter', 'write', 'Creates the first credit note number counter', body=True),
    E('list_credit_note_drafts', 'GET', '/creditNotes/drafts', 'read', 'Returns all credit note drafts for the company', paged=True),
    E('create_credit_note_draft', 'POST', '/creditNotes/drafts', 'write', 'Creates a new credit note draft', body=True),
    E('get_credit_note_draft', 'GET', '/creditNotes/drafts/{draftId}', 'read', 'Returns a specific credit note draft'),
    E('update_credit_note_draft', 'PUT', '/creditNotes/drafts/{draftId}', 'destructive', 'Updates a credit note draft. Replaces the whole record: send every field you want to keep.', body=True),
    E('delete_credit_note_draft', 'DELETE', '/creditNotes/drafts/{draftId}', 'destructive', 'Deletes a credit note draft'),
    E('create_credit_note_from_draft', 'POST', '/creditNotes/drafts/{draftId}/createCreditNote', 'write', 'Creates a finalized credit note from a draft'),
    E('list_invoices', 'GET', '/invoices', 'read', 'Returns all invoices for the company', paged=True, filters=('issueDate', 'issueDateLe', 'issueDateLt', 'issueDateGe', 'issueDateGt', 'lastModified', 'lastModifiedLe', 'lastModifiedLt', 'lastModifiedGe', 'lastModifiedGt', 'dueDate', 'dueDateLe', 'dueDateLt', 'dueDateGe', 'dueDateGt', 'customerId', 'settled', 'orderReference', 'invoiceDraftUuid', 'invoiceNumber')),
    E('create_invoice', 'POST', '/invoices', 'write', "Creates a new invoice. Amounts are in NOK øre (cents). Each line's incomeAccount must exist in the chart of accounts and match its vatType (e.g. 3000 accepts only HIGH, 3100 only EXEMPT, 3200 only OUTSIDE).", body=True),
    E('get_invoice', 'GET', '/invoices/{invoiceId}', 'read', 'Returns a specific invoice by ID'),
    E('update_invoice', 'PATCH', '/invoices/{invoiceId}', 'write', 'Updates an invoice (due date and/or manual send status)', body=True),
    E('send_invoice', 'POST', '/invoices/send', 'write', 'Sends an invoice via email and/or EHF', body=True),
    E('get_invoice_counter', 'GET', '/invoices/counter', 'read', 'Retrieves the current invoice number counter'),
    E('create_invoice_counter', 'POST', '/invoices/counter', 'write', 'Creates the first invoice number counter', body=True),
    E('list_invoice_drafts', 'GET', '/invoices/drafts', 'read', 'Returns all invoice drafts for the company', paged=True, filters=('orderReference', 'uuid')),
    E('create_invoice_draft', 'POST', '/invoices/drafts', 'write', 'Creates a new invoice draft', body=True),
    E('get_invoice_draft', 'GET', '/invoices/drafts/{draftId}', 'read', 'Returns a specific invoice draft'),
    E('update_invoice_draft', 'PUT', '/invoices/drafts/{draftId}', 'destructive', 'Updates an existing invoice draft. Replaces the whole record: send every field you want to keep.', body=True),
    E('delete_invoice_draft', 'DELETE', '/invoices/drafts/{draftId}', 'destructive', 'Deletes an invoice draft'),
    E('create_recurring_invoice_from_draft', 'POST', '/invoices/drafts/{draftId}/createRecurringInvoice', 'write', 'Creates a recurring invoice from an existing draft of type repeating_invoice (activates it)'),
    E('create_invoice_from_draft', 'POST', '/invoices/drafts/{draftId}/createInvoice', 'write', 'Creates a finalized invoice from a draft'),
    E('list_journal_entries', 'GET', '/journalEntries', 'read', 'Returns all general journal entries for the company', paged=True, filters=('date', 'dateLe', 'dateLt', 'dateGe', 'dateGt', 'lastModified', 'lastModifiedLe', 'lastModifiedLt', 'lastModifiedGe', 'lastModifiedGt', 'createdDate', 'createdDateLe', 'createdDateLt', 'createdDateGe', 'createdDateGt')),
    E('get_journal_entry', 'GET', '/journalEntries/{journalEntryId}', 'read', 'Returns a specific journal entry by ID'),
    E('create_journal_entry', 'POST', '/generalJournalEntries', 'write', JOURNAL_ENTRY_DESCRIPTION, body=True, validate='journal_balance'),
    E('create_product_sales_report', 'POST', '/products/salesReport', 'write', 'Creates a product sales report for a date range', body=True),
    E('list_inbox', 'GET', '/inbox', 'read', 'Returns incoming documents in the company inbox', paged=True, filters=('sortBy', 'status', 'name')),
    E('get_inbox_document', 'GET', '/inbox/{inboxDocumentId}', 'read', 'Returns a specific inbox document by ID'),
    E('delete_inbox_document', 'DELETE', '/inbox/{inboxDocumentId}', 'destructive', 'Deletes an inbox document'),
    E('list_ehf_documents', 'GET', '/ehf', 'read', 'Returns received EHF (electronic invoice) documents', paged=True, filters=('sortBy', 'status', 'issueDate', 'issueDateLe', 'issueDateLt', 'issueDateGe', 'issueDateGt')),
    E('get_ehf_document', 'GET', '/ehf/{ehfDocumentId}', 'read', 'Returns a specific received EHF document by ID'),
    E('list_offers', 'GET', '/offers', 'read', 'Returns all offers for the company', paged=True),
    E('get_offer', 'GET', '/offers/{offerId}', 'read', 'Returns a specific offer by ID'),
    E('get_offer_counter', 'GET', '/offers/counter', 'read', 'Retrieves the current offer number counter'),
    E('create_offer_counter', 'POST', '/offers/counter', 'write', 'Creates the first offer number counter', body=True),
    E('list_offer_drafts', 'GET', '/offers/drafts', 'read', 'Returns all offer drafts for the company', paged=True),
    E('create_offer_draft', 'POST', '/offers/drafts', 'write', 'Creates a new offer draft', body=True),
    E('get_offer_draft', 'GET', '/offers/drafts/{draftId}', 'read', 'Returns a specific offer draft'),
    E('update_offer_draft', 'PUT', '/offers/drafts/{draftId}', 'destructive', 'Updates an offer draft. Replaces the whole record: send every field you want to keep.', body=True),
    E('delete_offer_draft', 'DELETE', '/offers/drafts/{draftId}', 'destructive', 'Deletes an offer draft'),
    E('create_offer_from_draft', 'POST', '/offers/drafts/{draftId}/createOffer', 'write', 'Creates a finalized offer from a draft'),
    E('send_offer', 'POST', '/offers/send', 'write', 'Sends an offer by email, EHF, eFaktura, SMS or letter', body=True),
    E('list_order_confirmations', 'GET', '/orderConfirmations', 'read', 'Returns all order confirmations for the company', paged=True),
    E('get_order_confirmation', 'GET', '/orderConfirmations/{confirmationId}', 'read', 'Returns a specific order confirmation by ID'),
    E('get_order_confirmation_counter', 'GET', '/orderConfirmations/counter', 'read', 'Retrieves the order confirmation number counter'),
    E('create_order_confirmation_counter', 'POST', '/orderConfirmations/counter', 'write', 'Creates the first order confirmation number counter', body=True),
    E('create_invoice_draft_from_order_confirmation', 'POST', '/orderConfirmations/{confirmationId}/createInvoiceDraft', 'write', 'Creates an invoice draft from an order confirmation'),
    E('list_order_confirmation_drafts', 'GET', '/orderConfirmations/drafts', 'read', 'Returns all order confirmation drafts for the company', paged=True),
    E('create_order_confirmation_draft', 'POST', '/orderConfirmations/drafts', 'write', 'Creates a new order confirmation draft (set type to "order_confirmation")', body=True),
    E('get_order_confirmation_draft', 'GET', '/orderConfirmations/drafts/{draftId}', 'read', 'Returns a specific order confirmation draft'),
    E('update_order_confirmation_draft', 'PUT', '/orderConfirmations/drafts/{draftId}', 'destructive', 'Updates an order confirmation draft. Replaces the whole record: send every field you want to keep.', body=True),
    E('delete_order_confirmation_draft', 'DELETE', '/orderConfirmations/drafts/{draftId}', 'destructive', 'Deletes an order confirmation draft'),
    E('create_order_confirmation_from_draft', 'POST', '/orderConfirmations/drafts/{draftId}/createOrderConfirmation', 'write', 'Creates a finalized order confirmation from a draft'),
    E('list_products', 'GET', '/products', 'read', 'Returns all products for the company', paged=True, filters=('createdDate', 'createdDateLe', 'createdDateLt', 'createdDateGe', 'createdDateGt', 'lastModified', 'lastModifiedLe', 'lastModifiedLt', 'lastModifiedGe', 'lastModifiedGt', 'name', 'productNumber', 'active')),
    E('create_product', 'POST', '/products', 'write', 'Creates a new product. Amounts are in cents. Fiken enforces account/VAT combinations: incomeAccount must exist in the chart of accounts and match vatType (e.g. 3000 accepts only HIGH, 3100 only EXEMPT, 3200 only OUTSIDE).', body=True),
    E('get_product', 'GET', '/products/{productId}', 'read', 'Returns a specific product by ID'),
    E('update_product', 'PUT', '/products/{productId}', 'destructive', 'Updates an existing product (full replacement; send all fields). Replaces the whole record: send every field you want to keep.', body=True),
    E('delete_product', 'DELETE', '/products/{productId}', 'destructive', 'Deletes a product'),
    E('list_purchases', 'GET', '/purchases', 'read', 'Returns all purchases for the company', paged=True, filters=('date', 'dateLe', 'dateLt', 'dateGe', 'dateGt', 'lastModified', 'lastModifiedLe', 'lastModifiedLt', 'lastModifiedGe', 'lastModifiedGt', 'sortBy', 'paid', 'settledDate', 'settledDateLe', 'settledDateLt', 'settledDateGe', 'settledDateGt', 'contactId')),
    E('create_purchase', 'POST', '/purchases', 'write', 'Creates a new purchase. Amounts in NOK øre. Fiken enforces account/VAT combinations: the account must exist in the chart of accounts (e.g. 7350 accepts only vatType NONE), and cash purchases (paid immediately) require paymentDate equal to date.', body=True),
    E('get_purchase', 'GET', '/purchases/{purchaseId}', 'read', 'Returns a specific purchase by ID'),
    E('delete_purchase', 'PATCH', '/purchases/{purchaseId}/delete', 'destructive', 'Marks a purchase as deleted. The purchase is not removed; a reverse transaction is created', required_query=('description',)),
    E('list_purchase_drafts', 'GET', '/purchases/drafts', 'read', 'Returns all purchase drafts for the company', paged=True),
    E('create_purchase_draft', 'POST', '/purchases/drafts', 'write', 'Creates a new purchase draft', body=True),
    E('get_purchase_draft', 'GET', '/purchases/drafts/{draftId}', 'read', 'Returns a specific purchase draft'),
    E('update_purchase_draft', 'PUT', '/purchases/drafts/{draftId}', 'destructive', 'Updates a purchase draft. Replaces the whole record: send every field you want to keep.', body=True),
    E('delete_purchase_draft', 'DELETE', '/purchases/drafts/{draftId}', 'destructive', 'Deletes a purchase draft'),
    E('create_purchase_from_draft', 'POST', '/purchases/drafts/{draftId}/createPurchase', 'write', 'Creates a finalized purchase from a draft'),
    E('get_purchase_payments', 'GET', '/purchases/{purchaseId}/payments', 'read', 'Returns all payments for a purchase'),
    E('create_purchase_payment', 'POST', '/purchases/{purchaseId}/payments', 'write', 'Creates a new payment for a purchase. Amounts in cents', body=True),
    E('get_purchase_payment', 'GET', '/purchases/{purchaseId}/payments/{paymentId}', 'read', 'Returns a specific payment on a purchase'),
    E('delete_purchase_payment', 'DELETE', '/purchases/{purchaseId}/payments/{paymentId}', 'destructive', 'Undoes a payment on a purchase ("Angre"). An open payment is deleted; a closed one gets a reverse transaction', optional_query=('description',)),
    E('get_purchase_accruals', 'GET', '/purchases/{purchaseId}/accruals', 'read', 'Returns all accruals (periodisering) set up on a purchase'),
    E('create_purchase_accrual', 'POST', '/purchases/{purchaseId}/accruals', 'write', 'Sets up an accrual for one line on a purchase, spreading the line amount evenly over monthly periods. Only lines on result accounts (3000-7999) can be accrued', body=True),
    E('get_purchase_accrual', 'GET', '/purchases/{purchaseId}/accruals/{accrualId}', 'read', 'Returns a specific accrual on a purchase'),
    E('delete_purchase_accrual', 'DELETE', '/purchases/{purchaseId}/accruals/{accrualId}', 'destructive', 'Deletes an accrual on a purchase'),
    E('list_recurring_invoices', 'GET', '/recurringInvoices', 'read', 'Returns all recurring invoices for the company', paged=True, filters=('customerId', 'active')),
    E('create_recurring_invoice', 'POST', '/recurringInvoices', 'write', 'Creates a recurring invoice ("Repeterende faktura") for a single customer. Amounts are in cents. The recurring invoice number series must already exist (create the first one in Fiken Web).', body=True),
    E('get_recurring_invoice', 'GET', '/recurringInvoices/{recurringInvoiceId}', 'read', 'Returns a specific recurring invoice by ID'),
    E('update_recurring_invoice_lines', 'PUT', '/recurringInvoices/{recurringInvoiceId}/lines', 'destructive', 'Replaces all invoice lines of a recurring invoice (applies to every job). Amounts are in cents. Replaces the whole record: send every field you want to keep.', body=True),
    E('update_recurring_invoice_frequency', 'PUT', '/recurringInvoices/{recurringInvoiceId}/frequency', 'destructive', 'Updates description, frequency and days-until-due of a recurring invoice (applies to every job). Replaces the whole record: send every field you want to keep.', body=True),
    E('add_recurring_invoice_job', 'POST', '/recurringInvoices/{recurringInvoiceId}/jobs', 'write', 'Adds a job (an additional customer) to a recurring invoice. Lines and settings are copied from an existing job. A customer can appear only once.', body=True),
    E('update_recurring_invoice_job_schedule', 'PUT', '/recurringInvoices/{recurringInvoiceId}/jobs/{jobId}/schedule', 'destructive', 'Updates the next date and/or end date of a single job. Replaces the whole record: send every field you want to keep.', body=True),
    E('update_recurring_invoice_job_recipients', 'PUT', '/recurringInvoices/{recurringInvoiceId}/jobs/{jobId}/recipients', 'destructive', 'Replaces the delivery configuration of a job. Any channel not included is turned off; at least one recipient is required. EHF, eFaktura and SMS are mutually exclusive. Replaces the whole record: send every field you want to keep.', body=True),
    E('list_sales', 'GET', '/sales', 'read', 'Returns all sales for the company', paged=True, filters=('date', 'dateLe', 'dateLt', 'dateGe', 'dateGt', 'lastModified', 'lastModifiedLe', 'lastModifiedLt', 'lastModifiedGe', 'lastModifiedGt', 'contactId', 'settled', 'saleNumber')),
    E('create_sale', 'POST', '/sales', 'write', 'Creates a new sale. Amounts in NOK øre. Fiken enforces account/VAT combinations: the account must exist in the chart of accounts (e.g. 3000 accepts only vatType HIGH, 3100 only EXEMPT, 3200 only OUTSIDE), and cash sales (kind cash_sale) require paymentDate equal to date.', body=True),
    E('get_sale', 'GET', '/sales/{saleId}', 'read', 'Returns a specific sale by ID'),
    E('delete_sale', 'PATCH', '/sales/{saleId}/delete', 'destructive', 'Marks a sale as deleted. The sale is not removed; a reverse transaction is created and its deleted flag is set', required_query=('description',)),
    E('list_sale_drafts', 'GET', '/sales/drafts', 'read', 'Returns all sale drafts for the company', paged=True),
    E('create_sale_draft', 'POST', '/sales/drafts', 'write', 'Creates a new sale draft', body=True),
    E('get_sale_draft', 'GET', '/sales/drafts/{draftId}', 'read', 'Returns a specific sale draft'),
    E('update_sale_draft', 'PUT', '/sales/drafts/{draftId}', 'destructive', 'Updates a sale draft. Replaces the whole record: send every field you want to keep.', body=True),
    E('delete_sale_draft', 'DELETE', '/sales/drafts/{draftId}', 'destructive', 'Deletes a sale draft'),
    E('create_sale_from_draft', 'POST', '/sales/drafts/{draftId}/createSale', 'write', 'Creates a finalized sale from a draft'),
    E('settle_sale', 'PATCH', '/sales/{saleId}/settled', 'destructive', 'Marks a sale as settled without payment ("sett til oppgjort uten betaling"). Send a new settledDate to change the settlement date', required_query=('settledDate',)),
    E('write_off_sale', 'PATCH', '/sales/{saleId}/writeOff', 'destructive', 'Registers a write-off (tapsføring) for a sale. The sale must not be a cash sale, already written off, settled or deleted, and must have an outstanding balance. The write-off date must be after the sale date', body=True),
    E('get_sale_payments', 'GET', '/sales/{saleId}/payments', 'read', 'Returns all payments for a sale'),
    E('create_sale_payment', 'POST', '/sales/{saleId}/payments', 'write', 'Creates a new payment for a sale. Amounts in cents', body=True),
    E('get_sale_payment', 'GET', '/sales/{saleId}/payments/{paymentId}', 'read', 'Returns a specific payment on a sale'),
    E('delete_sale_payment', 'DELETE', '/sales/{saleId}/payments/{paymentId}', 'destructive', 'Undoes a payment on a sale ("Angre"). An open payment is deleted; a closed one gets a reverse transaction. Fails if already deleted, on a written-off sale, or related to debt collection', optional_query=('description',)),
    E('get_sale_accruals', 'GET', '/sales/{saleId}/accruals', 'read', 'Returns all accruals (periodisering) set up on a sale'),
    E('create_sale_accrual', 'POST', '/sales/{saleId}/accruals', 'write', 'Sets up an accrual for one line on a sale, spreading the line amount evenly over monthly periods. Only lines on result accounts (3000-7999) can be accrued; sales with sales-cost lines are not supported', body=True),
    E('get_sale_accrual', 'GET', '/sales/{saleId}/accruals/{accrualId}', 'read', 'Returns a specific accrual on a sale'),
    E('delete_sale_accrual', 'DELETE', '/sales/{saleId}/accruals/{accrualId}', 'destructive', 'Deletes an accrual on a sale'),
    E('list_projects', 'GET', '/projects', 'read', 'Returns all projects for the company', paged=True, filters=('completed', 'name', 'number')),
    E('create_project', 'POST', '/projects', 'write', 'Creates a new project', body=True),
    E('get_project', 'GET', '/projects/{projectId}', 'read', 'Returns a specific project by ID'),
    E('update_project', 'PATCH', '/projects/{projectId}', 'write', 'Updates a project (PATCH: only the supplied fields are changed)', body=True),
    E('delete_project', 'DELETE', '/projects/{projectId}', 'destructive', 'Deletes a project'),
    E('list_activities', 'GET', '/activities', 'read', 'Returns all activity types for the company', paged=True, filters=('name', 'archived')),
    E('create_activity', 'POST', '/activities', 'write', 'Creates a new activity type for time tracking', body=True),
    E('get_activity', 'GET', '/activities/{activityId}', 'read', 'Returns a specific activity by ID'),
    E('update_activity', 'PATCH', '/activities/{activityId}', 'write', 'Updates an activity (PATCH: only the supplied fields are changed)', body=True),
    E('delete_activity', 'DELETE', '/activities/{activityId}', 'destructive', 'Deletes an activity'),
    E('list_time_entries', 'GET', '/timeEntries', 'read', 'Returns time entries for the company', paged=True, filters=('date', 'dateGe', 'dateLe', 'projectId', 'activityId', 'timeUserId', 'invoiced', 'lastModifiedGe', 'lastModifiedLe')),
    E('create_time_entry', 'POST', '/timeEntries', 'write', 'Creates a new time entry', body=True),
    E('create_invoice_draft_from_time_entries', 'POST', '/timeEntries/createInvoiceDraft', 'write', 'Creates an invoice draft from existing time entries. All time entries must be billable and not yet invoiced.', body=True),
    E('get_time_entry', 'GET', '/timeEntries/{timeEntryId}', 'read', 'Returns a specific time entry by ID'),
    E('update_time_entry', 'PATCH', '/timeEntries/{timeEntryId}', 'write', 'Updates a time entry (PATCH: only the supplied fields are changed)', body=True),
    E('delete_time_entry', 'DELETE', '/timeEntries/{timeEntryId}', 'destructive', 'Deletes a time entry'),
    E('list_time_users', 'GET', '/timeUsers', 'read', 'Returns all time-tracking users for the company', paged=True, filters=('name', 'email')),
    E('get_time_user', 'GET', '/timeUsers/{timeUserId}', 'read', 'Returns a specific time-tracking user by ID'),
    E('list_transactions', 'GET', '/transactions', 'read', 'Returns all transactions for the company', paged=True, filters=('lastModified', 'lastModifiedLe', 'lastModifiedLt', 'lastModifiedGe', 'lastModifiedGt', 'createdDate', 'createdDateLe', 'createdDateLt', 'createdDateGe', 'createdDateGt')),
    E('get_transaction', 'GET', '/transactions/{transactionId}', 'read', 'Returns a specific transaction by ID'),
    E('delete_transaction', 'PATCH', '/transactions/{transactionId}/delete', 'destructive', 'Marks a transaction as deleted and creates a reversing transaction', required_query=('description',)),
    E('get_user', 'GET', '/user', 'read', 'Returns information about the authenticated Fiken user', company=False),
    E('list_companies', 'GET', '/companies', 'read', 'Returns all companies the authenticated user has access to', company=False, paged=True, filters=('sortBy',)),
    E('get_company', 'GET', '', 'read', 'Returns details of the company'),

)
