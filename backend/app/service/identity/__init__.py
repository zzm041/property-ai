"""Identity services: authentication, RBAC, residents and properties.

S5 of the package refactor moved these modules here byte-for-byte. The
legacy `app.service.<module>` files stay behind as re-export shims until
S8 deletes them; this package does not re-export anything itself, because
these modules import one another and a star-export here would turn those
edges into import cycles at package load time.
"""
