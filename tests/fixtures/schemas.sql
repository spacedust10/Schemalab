-- In the style of AdventureWorks for Postgres: tables nested in CREATE SCHEMA, domain types,
-- keys added later, rows loaded by psql from CSV files that are not part of the file.
\pset tuples_only on
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE DOMAIN "Name" varchar(50) NULL;
CREATE DOMAIN "Flag" boolean NOT NULL;

-- The Person schema
/* tables first */
CREATE SCHEMA Person
  CREATE TABLE BusinessEntity(
    BusinessEntityID SERIAL, --  NOT FOR REPLICATION
    ModifiedDate TIMESTAMP NOT NULL CONSTRAINT "DF_BusinessEntity_ModifiedDate" DEFAULT (NOW())
  )
  CREATE TABLE Person(
    BusinessEntityID INT NOT NULL,
    FirstName "Name" NOT NULL,
    Active "Flag" NOT NULL,
    CONSTRAINT "CK_Person_Name" CHECK (FirstName <> 'created')
  );

CREATE SCHEMA Sales
  CREATE TABLE Customer(
    CustomerID SERIAL,
    PersonID INT NULL,
    Note varchar(20) DEFAULT 'CREATE TABLE x'
  );

CREATE SCHEMA crm
  CREATE TABLE Customer(
    CustomerID INT NOT NULL,
    SalesCustomerID INT NOT NULL
  );

\copy Person.BusinessEntity FROM './data/BusinessEntity.csv' DELIMITER E'\t' CSV;
\copy Person.Person FROM './data/Person.csv' DELIMITER E'\t' CSV;

ALTER TABLE Person.BusinessEntity ADD
    CONSTRAINT "PK_BusinessEntity_BusinessEntityID" PRIMARY KEY (BusinessEntityID);
ALTER TABLE Person.Person ADD
    CONSTRAINT "PK_Person_BusinessEntityID" PRIMARY KEY (BusinessEntityID);
ALTER TABLE Sales.Customer ADD
    CONSTRAINT "PK_Customer_CustomerID" PRIMARY KEY (CustomerID);
ALTER TABLE crm.Customer ADD
    CONSTRAINT "PK_crm_Customer" PRIMARY KEY (CustomerID);
ALTER TABLE Person.Person ADD
    CONSTRAINT "FK_Person_BusinessEntity" FOREIGN KEY (BusinessEntityID)
    REFERENCES Person.BusinessEntity(BusinessEntityID);
ALTER TABLE Sales.Customer ADD
    CONSTRAINT "FK_Customer_Person" FOREIGN KEY (PersonID) REFERENCES Person.Person(BusinessEntityID);
ALTER TABLE crm.Customer ADD
    CONSTRAINT "FK_crm_Customer_Sales" FOREIGN KEY (SalesCustomerID) REFERENCES Sales.Customer(CustomerID);

CREATE SCHEMA pe
  CREATE VIEW p AS SELECT businessentityid AS id, * FROM person.person;
