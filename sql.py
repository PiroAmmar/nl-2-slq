import sqlite3

conn = sqlite3.connect('students.db')
cursor = conn.cursor()
table_info = '''
CREATE TABLE STUDENTS (
    roll_no VARCHAR(6) PRIMARY KEY,
    name VARCHAR(25) NOT NULL,
    class VARCHAR(25) NOT NULL,
    section VARCHAR(25) NOT NULL,
    marks INT NOT NULL
);
'''

# cursor.execute(table_info)

cursor.execute('''Insert into STUDENTS values('0620','Ayesh','7','B','78')''')
cursor.execute('''Insert into STUDENTS values('0740','Ammar','7','J','98')''')
cursor.execute('''Insert into STUDENTS values('0930','Areeb','7','J','92')''')
cursor.execute('''Insert into STUDENTS values('0842','Sarim','7','F','86')''')
cursor.execute('''Insert into STUDENTS values('0617','Aon','7','J','58')''')

print("the inserted records are: ")

data=cursor.execute('''Select * from STUDENTS''')

for row in data:
    print(row)

conn.commit()
conn.close()