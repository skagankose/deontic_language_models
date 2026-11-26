"""Domain vocabularies for the synthetic benchmark.

Each domain gives an issue literal (the query), its opposite (the head used by exceptions), a
pool of condition predicates with a case-file sentence and a condition phrase, intermediate
concepts (derived predicates used to build inference chains), and a distractor rule on a
different issue. Two domains are deontic (Perm/Forb and Obl/Perm¬): their decisions use the
D-axiom duality so that an accepted Forb(a) answers "may a?" with Negative.
"""

SUPPLY = {
    "name": "supply",
    # +1: condition tends to support the issue, -1: tends to defeat it
    "lean": {"ValidContract": 1, "Delay": 1, "ForceMajeure": -1, "GrossNegligence": 1, "NoticeMissing": -1, "NoticeWaived": 1, "CustomerCaused": -1, "PartialDelivery": -1, "PenaltyCapReached": -1, "CriticalOrder": 1, "ClaimLate": -1, "ExcusableDelay": -1, "MaterialDelay": 1},
    "title": "Supply Agreement",
    "const": "case",
    "query_nl": "Can the customer claim the contractual delay penalty?",
    "issue": "EnforceablePenalty(case)",
    "opposite": "¬EnforceablePenalty(case)",
    "issue_gloss": "the customer is entitled to claim the contractual delay penalty",
    "pos": "the customer is entitled to the delay penalty",
    "neg": "the customer is not entitled to the delay penalty",
    "deontic_duality": False,
    "conditions": {
        "ValidContract": ("The parties concluded a valid supply agreement.", "the supply agreement is valid"),
        "Delay": ("The supplier delivered the goods after the agreed delivery date.", "the supplier delivers late"),
        "ForceMajeure": ("A regional flood, a force majeure event, disrupted the supplier's plant.", "the delay is caused by force majeure"),
        "GrossNegligence": ("The supplier's gross negligence contributed to the delay.", "the supplier acted with gross negligence"),
        "NoticeMissing": ("The customer did not send a written delay notice within ten days.", "the customer fails to send a written delay notice within ten days"),
        "NoticeWaived": ("The supplier confirmed the delay in writing.", "the supplier has confirmed the delay in writing"),
        "CustomerCaused": ("The delay was caused by late specifications from the customer.", "the customer caused the delay"),
        "PartialDelivery": ("The supplier delivered part of the order on time.", "part of the order was delivered on time"),
        "PenaltyCapReached": ("Penalties paid this year have already reached the annual cap.", "the annual penalty cap has been reached"),
        "CriticalOrder": ("The purchase order designated the order as critical.", "the order is designated as critical"),
        "ClaimLate": ("The customer filed its penalty claim 120 days after delivery.", "the claim is filed more than 90 days after delivery"),
    },
    "intermediates": {
        "ExcusableDelay": ("the delay is excusable", "the delay is an excusable delay"),
        "MaterialDelay": ("the delay is material", "the delay is a material delay"),
    },
    "distractor": {"head": "InterestAccrues(case)", "phrase": "interest accrues on the overdue invoice",
                   "gloss": "interest accrues on an overdue invoice",
                   "cond": ("LatePayment", "The customer paid one invoice late.", "the customer pays an invoice late")},
}

LEASE = {
    "name": "lease",
    # +1: condition tends to support the issue, -1: tends to defeat it
    "lean": {"Defect": 1, "DefectReported": 1, "TenantCaused": -1, "QuickRepair": -1, "KnownAtSigning": -1, "WaiverSigned": -1, "ConsumerLease": 1, "RentArrears": -1, "AccessRefused": -1, "HealthHazard": 1, "MinorDefect": -1, "LandlordResponsible": 1, "CooperationBreached": -1},
    "title": "Residential Lease",
    "const": "tenancy",
    "query_nl": "Is the tenant entitled to a rent reduction?",
    "issue": "RentReduction(tenancy)",
    "opposite": "¬RentReduction(tenancy)",
    "issue_gloss": "the tenant is entitled to a rent reduction",
    "pos": "the tenant is entitled to a rent reduction",
    "neg": "the tenant is not entitled to a rent reduction",
    "deontic_duality": False,
    "conditions": {
        "Defect": ("The heating system of the flat failed in January.", "the premises have a defect"),
        "DefectReported": ("The tenant reported the defect to the landlord in writing.", "the tenant has reported the defect in writing"),
        "TenantCaused": ("The defect was caused by the tenant's misuse of the boiler.", "the tenant caused the defect"),
        "QuickRepair": ("The landlord repaired the defect within seven days.", "the landlord repairs the defect within seven days"),
        "KnownAtSigning": ("The tenant knew about the defect when signing the lease.", "the tenant knew of the defect when signing"),
        "WaiverSigned": ("The tenant signed a waiver of rent-reduction rights.", "the tenant has signed a waiver of rent-reduction rights"),
        "ConsumerLease": ("The lease is a residential consumer lease.", "the lease is a residential consumer lease"),
        "RentArrears": ("The tenant is two months in arrears with rent.", "the tenant is in arrears with rent"),
        "AccessRefused": ("The tenant refused the landlord access for repairs.", "the tenant refused access for repairs"),
        "HealthHazard": ("The defect poses a health hazard.", "the defect poses a health hazard"),
        "MinorDefect": ("The defect only affects a storage room.", "the defect is minor"),
    },
    "intermediates": {
        "LandlordResponsible": ("the landlord is responsible for the defect", "the landlord is responsible for the defect"),
        "CooperationBreached": ("the tenant has breached the duty to cooperate", "the tenant has breached the duty to cooperate"),
    },
    "distractor": {"head": "LateFeeDue(tenancy)", "phrase": "a late fee is due",
                   "gloss": "a late fee is due under the lease",
                   "cond": ("LateRent", "The tenant paid the March rent late.", "the tenant pays rent late")},
}

DATA = {
    "name": "data",
    # +1: condition tends to support the issue, -1: tends to defeat it
    "lean": {"CustomerConsent": 1, "CourtOrder": 1, "Affiliate": 1, "SensitiveData": -1, "ConsentWithdrawn": -1, "Anonymised": 1, "OutsideEEA": -1, "AdequacyDecision": 1, "ThreatToLife": 1, "DpaSigned": 1, "AuditPending": -1, "LawfulBasis": 1, "RestrictedTransfer": -1},
    "title": "Data Processing Agreement",
    "const": "request",
    "query_nl": "May the provider disclose the customer data to the requesting party?",
    "issue": "Perm(Disclose(provider))",
    "opposite": "Forb(Disclose(provider))",
    "issue_gloss": "Disclose(provider): the provider discloses the customer data to the requesting party",
    "pos": "the provider may disclose the customer data to the requesting party",
    "neg": "the provider must not disclose the customer data to the requesting party",
    "deontic_duality": True,
    "conditions": {
        "CustomerConsent": ("The customer gave written consent to the disclosure.", "the customer has consented in writing"),
        "CourtOrder": ("A court issued an order requiring production of the data.", "a court order requires disclosure"),
        "Affiliate": ("The requesting party is an affiliate of the provider.", "the requester is an affiliate of the provider"),
        "SensitiveData": ("The requested records include health data.", "the data includes health or other special-category data"),
        "ConsentWithdrawn": ("The customer later withdrew its consent.", "the customer has withdrawn its consent"),
        "Anonymised": ("The provider anonymised the records before any transfer.", "the data is anonymised before transfer"),
        "OutsideEEA": ("The requesting party is located outside the EEA.", "the requester is located outside the EEA"),
        "AdequacyDecision": ("An adequacy decision covers the requester's country.", "an adequacy decision covers the requester's country"),
        "ThreatToLife": ("The request concerns an imminent threat to life.", "the request concerns an imminent threat to life"),
        "DpaSigned": ("A data processing agreement with the requester is in force.", "a data processing agreement with the requester is in force"),
        "AuditPending": ("A regulatory audit of the provider is pending.", "a regulatory audit is pending"),
    },
    "intermediates": {
        "LawfulBasis": ("there is a lawful basis for the transfer", "there is a lawful basis for the transfer"),
        "RestrictedTransfer": ("the transfer is a restricted transfer", "the transfer is a restricted transfer"),
    },
    "distractor": {"head": "LoggedRequest(request)", "phrase": "the request is a logged request",
                   "gloss": "the request is a logged request",
                   "cond": ("RequestReceived", "The provider received a formal written request.", "a formal written request is received")},
}

EMPLOYMENT = {
    "name": "employment",
    # +1: condition tends to support the issue, -1: tends to defeat it
    "lean": {"ExtraHours": 1, "OvertimeOrdered": 1, "ExecutiveRole": -1, "SalaryAboveThreshold": -1, "TimeOffGranted": -1, "ClaimTimeBarred": -1, "CollectiveAgreement": 1, "EmergencyWork": 1, "Flexitime": -1, "HoursRecorded": 1, "VoluntaryWork": -1, "OvertimeWorked": 1, "ExemptEmployee": -1},
    "title": "Employment Contract",
    "const": "claim",
    "query_nl": "Must the employer pay overtime compensation?",
    "issue": "Obl(PayOvertime(employer))",
    "opposite": "Perm(¬PayOvertime(employer))",
    "issue_gloss": "PayOvertime(employer): the employer pays overtime compensation to the employee",
    "pos": "the employer must pay overtime compensation",
    "neg": "the employer is not required to pay overtime compensation",
    "deontic_duality": True,
    "conditions": {
        "ExtraHours": ("The employee worked 12 hours beyond the contractual schedule last month.", "the employee works beyond the contractual hours"),
        "OvertimeOrdered": ("A manager ordered the additional hours in writing.", "the additional hours were ordered by a manager"),
        "ExecutiveRole": ("The employee holds a senior executive position.", "the employee holds a senior executive position"),
        "SalaryAboveThreshold": ("The employee's salary exceeds the statutory overtime threshold.", "the employee's salary exceeds the statutory threshold"),
        "TimeOffGranted": ("The employer granted equivalent time off in lieu.", "equivalent time off in lieu has been granted"),
        "ClaimTimeBarred": ("The employee raised the claim after the six-month deadline.", "the claim is raised after the six-month deadline"),
        "CollectiveAgreement": ("A collective agreement covers the employee.", "a collective agreement applies"),
        "EmergencyWork": ("The hours were worked during a declared operational emergency.", "the hours were worked during a declared emergency"),
        "Flexitime": ("The employee participates in a flexitime scheme.", "the employee participates in a flexitime scheme"),
        "HoursRecorded": ("The extra hours were recorded in the time-tracking system.", "the hours were recorded in the time-tracking system"),
        "VoluntaryWork": ("The employee worked the extra hours voluntarily without being asked.", "the extra hours were worked voluntarily"),
    },
    "intermediates": {
        "OvertimeWorked": ("overtime has been worked", "overtime has been worked"),
        "ExemptEmployee": ("the employee is an exempt employee", "the employee is an exempt employee"),
    },
    "distractor": {"head": "MealAllowanceDue(claim)", "phrase": "a meal allowance is due",
                   "gloss": "a meal allowance is due to the employee",
                   "cond": ("LateShift", "The employee worked a shift ending after 22:00.", "a shift ends after 22:00")},
}

DOMAINS = {d["name"]: d for d in (SUPPLY, LEASE, DATA, EMPLOYMENT)}
